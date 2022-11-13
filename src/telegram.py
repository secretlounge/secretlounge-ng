import telebot
import logging
import time
import json
import re

import src.core as core
import src.replies as rp
from src.util import MutablePriorityQueue, genTripcode
from src.globals import *

# module constants
MEDIA_FILTER_TYPES = ("photo", "animation", "document", "video", "video_note", "sticker")
CAPTIONABLE_TYPES = ("photo", "audio", "animation", "document", "video", "voice")
HIDE_FORWARD_FROM = set([
	"anonymize_bot", "anonfacebot", "anonymousforwarderbot", "anonomiserbot",
	"anonymous_forwarder_nashenasbot", "anonymous_forward_bot", "mirroring_bot",
	"anonymizbot", "forwardscoverbot", "anonymousmcjnbot", "mirroringbot",
	"anonymousforwarder_bot", "anonymousforwardbot", "anonymous_forwarder_bot",
	"anonymousforwardsbot", "hiddenlybot", "forwardcoveredbot", "anonym2bot",
	"antiforwardedbot", "noforward_bot", "anonymous_telegram_bot",
	"forwards_cover_bot", "forwardshidebot", "forwardscoversbot",
	"noforwardssourcebot", "antiforwarded_v2_bot", "forwardcoverzbot",
])
VENUE_PROPS = ("title", "address", "foursquare_id", "foursquare_type", "google_place_id", "google_place_type")

# module variables
bot = None
db = None
ch = None
message_queue = None
registered_commands = {}

# settings
allow_documents = None
linked_network: dict = None

def init(config, _db, _ch):
	global bot, db, ch, message_queue, allow_documents, linked_network
	if config["bot_token"] == "":
		logging.error("No telegram token specified.")
		exit(1)

	logging.getLogger("urllib3").setLevel(logging.WARNING) # very noisy with debug otherwise
	telebot.apihelper.READ_TIMEOUT = 20

	bot = telebot.TeleBot(config["bot_token"], threaded=False)
	db = _db
	ch = _ch
	message_queue = MutablePriorityQueue()

	allow_contacts = config["allow_contacts"]
	allow_documents = config["allow_documents"]
	linked_network = config.get("linked_network")
	if linked_network is not None and not isinstance(linked_network, dict):
		logging.error("Wrong type for 'linked_network'")
		exit(1)

	types = ["text", "location", "venue"]
	if allow_contacts:
		types += ["contact"]
	if allow_documents:
		types += ["document"]
	types += ["animation", "audio", "photo", "sticker", "video", "video_note", "voice"]

	cmds = [
		"start", "stop", "users", "info", "motd", "toggledebug", "togglekarma",
		"version", "source", "modhelp", "adminhelp", "modsay", "adminsay", "mod",
		"admin", "warn", "delete", "remove", "uncooldown", "blacklist", "s", "sign",
		"tripcode", "t", "tsign", "cleanup"
	]
	for c in cmds: # maps /<c> to the function cmd_<c>
		c = c.lower()
		registered_commands[c] = globals()["cmd_" + c]
	set_handler(relay, content_types=types)

def set_handler(func, *args, **kwargs):
	def wrapper(*args, **kwargs):
		try:
			func(*args, **kwargs)
		except Exception as e:
			logging.exception("Exception raised in event handler")
	bot.message_handler(*args, **kwargs)(wrapper)

def run():
	while True:
		try:
			bot.polling(none_stop=True, long_polling_timeout=45)
		except Exception as e:
			# you're not supposed to call .polling() more than once but I'm left with no choice
			logging.warning("%s while polling Telegram, retrying.", type(e).__name__)
			time.sleep(1)

def register_tasks(sched):
	# cache expiration
	def task():
		ids = ch.expire()
		if len(ids) == 0:
			return
		n = 0
		def f(item):
			nonlocal n
			if item.msid in ids:
				n += 1
				return True
			return False
		message_queue.delete(f)
		if n > 0:
			logging.warning("Failed to deliver %d messages before they expired from cache.", n)
	sched.register(task, hours=6) # (1/4) * cache duration

# Wraps a telegram user in a consistent class (used by core.py)
class UserContainer():
	def __init__(self, u):
		self.id = u.id
		self.username = u.username
		self.realname = u.first_name
		if u.last_name is not None:
			self.realname += " " + u.last_name

def split_command(text):
	if " " not in text:
		return text[1:].lower(), ""
	pos = text.find(" ")
	return text[1:pos].lower(), text[pos+1:].strip()

def takesArgument(optional=False):
	def f(func):
		def wrap(ev):
			_, arg = split_command(ev.text)
			if arg == "" and not optional:
				return
			return func(ev, arg)
		return wrap
	return f

def wrap_core(func, reply_to=False):
	def f(ev):
		m = func(UserContainer(ev.from_user))
		send_answer(ev, m, reply_to=reply_to)
	return f

def send_answer(ev, m, reply_to=False):
	if m is None:
		return
	elif isinstance(m, list):
		for m2 in m:
			send_answer(ev, m2, reply_to)
		return

	reply_to = ev.message_id if reply_to else None
	def f(ev=ev, m=m):
		while True:
			try:
				send_to_single_inner(ev.chat.id, m, reply_to=reply_to)
			except telebot.apihelper.ApiException as e:
				retry = check_telegram_exc(e, None)
				if retry:
					continue
				return
			break

	try:
		user = db.getUser(id=ev.from_user.id)
	except KeyError as e:
		user = None # happens on e.g. /start
	put_into_queue(user, None, f)

# TODO: find a better place for this
def allow_message_text(text):
	if text is None or text == "":
		return True
	# Mathematical Alphanumeric Symbols: has convincing looking bold text
	if any(0x1D400 <= ord(c) <= 0x1D7FF for c in text):
		return False
	return True

# determine spam score for message `ev`
def calc_spam_score(ev):
	if not allow_message_text(ev.text) or not allow_message_text(ev.caption):
		return 999

	s = SCORE_BASE_MESSAGE
	if (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None):
		s = SCORE_BASE_FORWARD

	if ev.content_type == "sticker":
		return SCORE_STICKER
	elif ev.content_type == "text":
		pass
	else:
		return s
	s += len(ev.text) * SCORE_TEXT_CHARACTER + ev.text.count("\n") * SCORE_TEXT_LINEBREAK
	return s

###

# Formatting for user messages, which are largely passed through as-is

class FormattedMessage():
	html: bool
	content: str
	def __init__(self, html, content):
		self.html = html
		self.content = content

class FormattedMessageBuilder():
	text_content: str
	# initialize builder with first argument that isn't None
	def __init__(self, *args):
		self.text_content = next(filter(lambda x: x is not None, args))
		self.inserts = {}
	def get_text(self):
		return self.text_content
	# insert `content` at `pos`, `html` indicates HTML or plaintext
	# if `pre` is set content will be inserted *before* existing insertions
	def insert(self, pos, content, html=False, pre=False):
		i = self.inserts.get(pos)
		if i is not None:
			cat = lambda a, b: (b + a) if pre else (a + b)
			# only turn insert into HTML if strictly necessary
			if i[0] == html:
				i = ( i[0], cat(i[1], content) )
			elif not i[0]:
				i = ( True, cat(escape_html(i[1]), content) )
			else: # not html
				i = ( True, cat(i[1], escape_html(content)) )
		else:
			i = (html, content)
		self.inserts[pos] = i
	def prepend(self, content, html=False):
		self.insert(0, content, html, True)
	def append(self, content, html=False):
		self.insert(len(self.text_content), content, html)
	def enclose(self, pos1, pos2, content_begin, content_end, html=False):
		self.insert(pos1, content_begin, html)
		self.insert(pos2, content_end, html, True)
	def build(self) -> FormattedMessage:
		if len(self.inserts) == 0:
			return
		html = any(i[0] for i in self.inserts.values())
		norm = lambda i: i[1] if i[0] == html else escape_html(i[1])
		s = ""
		for idx, c in enumerate(self.text_content):
			i = self.inserts.pop(idx, None)
			if i is not None:
				s += norm(i)
			s += escape_html(c) if html else c
		i = self.inserts.pop(len(self.text_content), None)
		if i is not None:
			s += norm(i)
		assert len(self.inserts) == 0
		return FormattedMessage(html, s)

# Append inline URLs from the message `ev` to `fmt` so they are preserved even
# if the original formatting is stripped
def formatter_replace_links(ev, fmt: FormattedMessageBuilder):
	entities = ev.caption_entities or ev.entities
	if entities is None:
		return
	for ent in entities:
		if ent.type == "text_link":
			if ent.url.startswith("tg://"):
				continue # doubt anyone needs these
			if "://t.me/" in ent.url and "?start=" in ent.url:
				continue # deep links look ugly and are likely not important
			fmt.append("\n(%s)" % ent.url)

# Add inline links for >>>/name/ syntax depending on configuration
def formatter_network_links(fmt: FormattedMessageBuilder):
	if not linked_network:
		return
	for m in re.finditer(r'>>>/([a-zA-Z0-9]+)/', fmt.get_text()):
		link = linked_network.get(m.group(1).lower())
		if link:
			# we use a tg:// URL here because it avoids web page preview
			fmt.enclose(m.start(), m.end(),
				"<a href=\"tg://resolve?domain=%s\">" % link, "</a>", True)

# Add signed message formatting for User `user` to `fmt`
def formatter_signed_message(user: core.User, fmt: FormattedMessageBuilder):
	fmt.append(" <a href=\"tg://user?id=%d\">" % user.id, True)
	fmt.append("~~" + user.getFormattedName())
	fmt.append("</a>", True)

# Add tripcode message formatting for User `user` to `fmt`
def formatter_tripcoded_message(user: core.User, fmt: FormattedMessageBuilder):
	tripname, tripcode = genTripcode(user.tripcode)
	# due to how prepend() works the string is built right-to-left
	fmt.prepend("</code>:\n", True)
	fmt.prepend(tripcode)
	fmt.prepend("</b> <code>", True)
	fmt.prepend(tripname)
	fmt.prepend("<b>", True)

###

# Message sending (queue-related)

class QueueItem():
	__slots__ = ("user_id", "msid", "func")
	def __init__(self, user, msid, func):
		self.user_id = None # who this item is being delivered to
		if user is not None:
			self.user_id = user.id
		self.msid = msid # message id connected to this item
		self.func = func
	def call(self):
		try:
			self.func()
		except Exception as e:
			logging.exception("Exception raised during queued message")

def get_priority_for(user):
	if user is None:
		# user doesn't exist (yet): handle as rank=0, lastActive=<now>
		# cf. User.getMessagePriority in database.py
		return max(RANKS.values()) << 16
	return user.getMessagePriority()

def put_into_queue(user, msid, f):
	message_queue.put(get_priority_for(user), QueueItem(user, msid, f))

def send_thread():
	while True:
		item = message_queue.get()
		item.call()

###

# Message sending (functions)

def is_forward(ev):
	return (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.forward_sender_name is not None)

def should_hide_forward(ev):
	# Hide forwards from anonymizing bots that have recently become popular.
	# The main reason is that the bot API heavily penalizes forwarding and the
	# 'Forwarded from Anonymize Bot' provides no additional/useful information.
	if ev.forward_from is not None:
		return (ev.forward_from.username or "").lower() in HIDE_FORWARD_FROM
	return False

def resend_message(chat_id, ev, reply_to=None, force_caption: FormattedMessage=None):
	if should_hide_forward(ev):
		pass
	elif is_forward(ev):
		# forward message instead of re-sending the contents
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	kwargs = {}
	if reply_to is not None:
		kwargs["reply_to_message_id"] = reply_to
		kwargs["allow_sending_without_reply"] = True
	if ev.content_type in CAPTIONABLE_TYPES:
		if force_caption is not None:
			kwargs["caption"] = force_caption.content
			if force_caption.html:
				kwargs["parse_mode"] = "HTML"
		else:
			kwargs["caption"] = ev.caption

	# re-send message based on content type
	if ev.content_type == "text":
		return bot.send_message(chat_id, ev.text, **kwargs)
	elif ev.content_type == "photo":
		photo = sorted(ev.photo, key=lambda e: e.width*e.height, reverse=True)[0]
		return bot.send_photo(chat_id, photo.file_id, **kwargs)
	elif ev.content_type == "audio":
		for prop in ("performer", "title"):
			kwargs[prop] = getattr(ev.audio, prop)
		return bot.send_audio(chat_id, ev.audio.file_id, **kwargs)
	elif ev.content_type == "animation":
		return bot.send_animation(chat_id, ev.animation.file_id, **kwargs)
	elif ev.content_type == "document":
		return bot.send_document(chat_id, ev.document.file_id, **kwargs)
	elif ev.content_type == "video":
		return bot.send_video(chat_id, ev.video.file_id, **kwargs)
	elif ev.content_type == "voice":
		return bot.send_voice(chat_id, ev.voice.file_id, **kwargs)
	elif ev.content_type == "video_note":
		return bot.send_video_note(chat_id, ev.video_note.file_id, **kwargs)
	elif ev.content_type == "location":
		for prop in ("latitude", "longitude", "horizontal_accuracy"):
			kwargs[prop] = getattr(ev.location, prop)
		return bot.send_location(chat_id, **kwargs)
	elif ev.content_type == "venue":
		kwargs["latitude"] = ev.venue.location.latitude
		kwargs["longitude"] = ev.venue.location.longitude
		for prop in VENUE_PROPS:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_venue(chat_id, **kwargs)
	elif ev.content_type == "contact":
		for prop in ("phone_number", "first_name", "last_name"):
			kwargs[prop] = getattr(ev.contact, prop)
		return bot.send_contact(chat_id, **kwargs)
	elif ev.content_type == "sticker":
		return bot.send_sticker(chat_id, ev.sticker.file_id, **kwargs)
	else:
		raise NotImplementedError("content_type = %s" % ev.content_type)

# send a message `ev` (multiple types possible) to Telegram ID `chat_id`
# returns the sent Telegram message
def send_to_single_inner(chat_id, ev, reply_to=None, force_caption=None):
	if isinstance(ev, rp.Reply):
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		if ev.type == rp.types.CUSTOM:
			kwargs2["disable_web_page_preview"] = True
		return bot.send_message(chat_id, rp.formatForTelegram(ev), parse_mode="HTML", **kwargs2)
	elif isinstance(ev, FormattedMessage):
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		if ev.html:
			kwargs2["parse_mode"] = "HTML"
		return bot.send_message(chat_id, ev.content, **kwargs2)

	return resend_message(chat_id, ev, reply_to=reply_to, force_caption=force_caption)

# queue sending of a single message `ev` (multiple types possible) to User `user`
# this includes saving of the sent message id to the cache mapping.
# `reply_msid` can be a msid of the message that will be replied to
# `force_caption` can be a FormattedMessage to set the caption for resent media
def send_to_single(ev, msid, user, *, reply_msid=None, force_caption=None):
	# set reply_to_message_id if applicable
	reply_to = None
	if reply_msid is not None:
		reply_to = ch.lookupMapping(user.id, msid=reply_msid)

	user_id = user.id
	def f():
		while True:
			try:
				ev2 = send_to_single_inner(user_id, ev, reply_to, force_caption)
			except telebot.apihelper.ApiException as e:
				retry = check_telegram_exc(e, user_id)
				if retry:
					continue
				return
			break
		ch.saveMapping(user_id, msid, ev2.message_id)
	put_into_queue(user, msid, f)

# delete message with `id` in Telegram chat `user_id`
def delete_message_inner(user_id, id):
	while True:
		try:
			bot.delete_message(user_id, id)
		except telebot.apihelper.ApiException as e:
			retry = check_telegram_exc(e, None)
			if retry:
				continue
			return
		break

# look at given Exception `e`, force-leave user if bot was blocked
# returns True if message sending should be retried
def check_telegram_exc(e, user_id):
	errmsgs = ["bot was blocked by the user", "user is deactivated",
		"PEER_ID_INVALID", "bot can't initiate conversation"]
	if any(msg in e.result.text for msg in errmsgs):
		if user_id is not None:
			core.force_user_leave(user_id)
		return False

	if "Too Many Requests" in e.result.text:
		d = json.loads(e.result.text)["parameters"]["retry_after"]
		d = min(d, 30) # supposedly this is in seconds, but you sometimes get 100 or even 2000
		logging.warning("API rate limit hit, waiting for %ds", d)
		time.sleep(d)
		return True # retry

	if "VOICE_MESSAGES_FORBIDDEN" in e.result.text:
		return False

	logging.exception("API exception")
	return False

####

# Event receiver: handles all things the core decides to do "on its own":
# e.g. karma notifications, deletion of messages, signed messages
# This does *not* include direct replies to commands or relaying of messages.

@core.registerReceiver
class MyReceiver(core.Receiver):
	@staticmethod
	def reply(m, msid, who, except_who, reply_msid):
		if who is not None:
			return send_to_single(m, msid, who, reply_msid=reply_msid)

		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who and not user.debugEnabled:
				continue
			send_to_single(m, msid, user, reply_msid=reply_msid)
	@staticmethod
	def delete(msids):
		msids_set = set(msids)
		# first stop actively delivering this message
		message_queue.delete(lambda item: item.msid in msids_set)
		# then delete all instances that have already been sent
		msids_owner = []
		for msid in msids:
			tmp = ch.getMessage(msid)
			msids_owner.append(None if tmp is None else tmp.user_id)
		assert len(msids_owner) == len(msids)
		# FIXME: there's a hard to avoid race condition here:
		# if a message is currently being sent, but finishes after we grab the
		# message ids it will never be deleted
		for user in db.iterateUsers():
			if not user.isJoined():
				continue

			for j, msid in enumerate(msids):
				if user.id == msids_owner[j] and not user.debugEnabled:
					continue
				id = ch.lookupMapping(user.id, msid=msid)
				if id is None:
					continue
				user_id = user.id
				def f(user_id=user_id, id=id):
					delete_message_inner(user_id, id)
				# msid=None here since this is a deletion, not a message being sent
				put_into_queue(user, None, f)
		# drop the mappings for this message so the id doesn't end up used e.g. for replies
		for msid in msids_set:
			ch.deleteMappings(msid)
	@staticmethod
	def stop_invoked(user, delete_out):
		# delete pending messages to be delivered *to* the user
		message_queue.delete(lambda item, user_id=user.id: item.user_id == user_id)
		if not delete_out:
			return
		# delete all pending messages written *by* the user too
		def f(item):
			if item.msid is None:
				return False
			cm = ch.getMessage(item.msid)
			if cm is None:
				return False
			return cm.user_id == user.id
		message_queue.delete(f)

####

cmd_start = wrap_core(core.user_join)
cmd_stop = wrap_core(core.user_leave)


cmd_users = wrap_core(core.get_users)

def cmd_info(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, core.get_info(c_user), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.get_info_mod(c_user, reply_msid), True)

@takesArgument(optional=True)
def cmd_motd(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_motd(c_user), reply_to=True)
	else:
		send_answer(ev, core.set_motd(c_user, arg), reply_to=True)

cmd_toggledebug = wrap_core(core.toggle_debug)
cmd_togglekarma = wrap_core(core.toggle_karma)

@takesArgument(optional=True)
def cmd_tripcode(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_tripcode(c_user))
	else:
		send_answer(ev, core.set_tripcode(c_user, arg))


def cmd_modhelp(ev):
	send_answer(ev, rp.Reply(rp.types.HELP_MODERATOR), True)

def cmd_adminhelp(ev):
	send_answer(ev, rp.Reply(rp.types.HELP_ADMIN), True)

def cmd_version(ev):
	send_answer(ev, rp.Reply(rp.types.PROGRAM_VERSION, version=VERSION), True)

cmd_source = cmd_version # alias


@takesArgument()
def cmd_modsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_mod_message(c_user, arg), True)

@takesArgument()
def cmd_adminsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_admin_message(c_user, arg), True)

@takesArgument()
def cmd_mod(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = arg.lstrip("@")
	send_answer(ev, core.promote_user(c_user, arg, RANKS.mod), True)

@takesArgument()
def cmd_admin(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = arg.lstrip("@")
	send_answer(ev, core.promote_user(c_user, arg, RANKS.admin), True)

def cmd_warn(ev, delete=False, only_delete=False):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	if only_delete:
		r = core.delete_message(c_user, reply_msid)
	else:
		r = core.warn_user(c_user, reply_msid, delete)
	send_answer(ev, r, True)

cmd_delete = lambda ev: cmd_warn(ev, delete=True)

cmd_remove = lambda ev: cmd_warn(ev, only_delete=True)

cmd_cleanup = wrap_core(core.cleanup_messages)

@takesArgument()
def cmd_uncooldown(ev, arg):
	c_user = UserContainer(ev.from_user)

	oid, username = None, None
	if len(arg) < 5:
		oid = arg # usernames can't be this short -> it's an id
	else:
		username = arg

	send_answer(ev, core.uncooldown_user(c_user, oid, username), True)

@takesArgument(optional=True)
def cmd_blacklist(ev, arg):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.blacklist_user(c_user, reply_msid, arg), True)

def plusone(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.give_karma(c_user, reply_msid), True)


def relay(ev):
	# handle commands and karma giving
	if ev.content_type == "text":
		if ev.text.startswith("/"):
			c, _ = split_command(ev.text)
			if c in registered_commands.keys():
				registered_commands[c](ev)
			return
		elif ev.text.strip() == "+1":
			return plusone(ev)
	# manually handle signing / tripcodes for media since captions don't count for commands
	if not is_forward(ev) and ev.content_type in CAPTIONABLE_TYPES and (ev.caption or "").startswith("/"):
		c, arg = split_command(ev.caption)
		if c in ("s", "sign"):
			return relay_inner(ev, caption_text=arg, signed=True)
		elif c in ("t", "tsign"):
			return relay_inner(ev, caption_text=arg, tripcode=True)

	relay_inner(ev)

# relay the message `ev` to other users in the chat
# `caption_text` can be a FormattedMessage that overrides the caption of media
# `signed` and `tripcode` indicate if the message is signed or tripcoded respectively
def relay_inner(ev, *, caption_text=None, signed=False, tripcode=False):
	is_media = is_forward(ev) or ev.content_type in MEDIA_FILTER_TYPES
	msid = core.prepare_user_message(UserContainer(ev.from_user), calc_spam_score(ev),
		is_media=is_media, signed=signed, tripcode=tripcode)
	if msid is None or isinstance(msid, rp.Reply):
		return send_answer(ev, msid) # don't relay message, instead reply

	user = db.getUser(id=ev.from_user.id)

	# for signed msgs: check user's forward privacy status first
	# FIXME? this is a possible bottleneck
	if signed:
		tchat = bot.get_chat(user.id)
		if tchat.has_private_forwards:
			return send_answer(ev, rp.Reply(rp.types.ERR_SIGN_PRIVACY))

	# apply text formatting to text or caption (if media)
	ev_tosend = ev
	force_caption = None
	if is_forward(ev):
		pass # leave message alone
	elif ev.content_type == "text" or ev.caption is not None or caption_text is not None:
		fmt = FormattedMessageBuilder(caption_text, ev.caption, ev.text)
		formatter_replace_links(ev, fmt)
		formatter_network_links(fmt)
		if signed:
			formatter_signed_message(user, fmt)
		elif tripcode:
			formatter_tripcoded_message(user, fmt)
		fmt = fmt.build()
		# either replace whole message or just the caption
		if ev.content_type == "text":
			ev_tosend = fmt or ev_tosend
		else:
			force_caption = fmt

	# find out which message is being replied to
	reply_msid = None
	if ev.reply_to_message is not None:
		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
		if reply_msid is None:
			logging.warning("Message replied to not found in cache")

	# relay message to all other users
	logging.debug("relay(): msid=%d reply_msid=%r", msid, reply_msid)
	for user2 in db.iterateUsers():
		if not user2.isJoined():
			continue
		if user2 == user and not user.debugEnabled:
			ch.saveMapping(user2.id, msid, ev.message_id)
			continue

		send_to_single(ev_tosend, msid, user2,
			reply_msid=reply_msid, force_caption=force_caption)

@takesArgument()
def cmd_sign(ev, arg):
	ev.text = arg
	relay_inner(ev, signed=True)

cmd_s = cmd_sign # alias

@takesArgument()
def cmd_tsign(ev, arg):
	ev.text = arg
	relay_inner(ev, tripcode=True)

cmd_t = cmd_tsign # alias
