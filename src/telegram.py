import telebot
import logging
import time
import re
import json

import src.core as core
import src.replies as rp
from src.util import MutablePriorityQueue
from src.globals import *

bot = None
db = None
ch = None
message_queue = None
registered_commands = {}

# settings regarding message relaying
allow_documents = None

def init(config, _db, _ch):
	global bot, db, ch, message_queue, allow_documents
	if config["bot_token"] == "":
		logging.error("No telegram token specified.")
		exit(1)

	logging.getLogger("urllib3").setLevel(logging.WARNING) # very noisy with debug otherwise
	bot = telebot.TeleBot(config["bot_token"], threaded=False)
	db = _db
	ch = _ch
	message_queue = MutablePriorityQueue()

	allow_contacts = config["allow_contacts"]
	allow_documents = config["allow_documents"]

	types = ["text", "location", "venue"]
	if allow_contacts:
		types += ["contact"]
	types += ["audio", "document", "photo", "sticker", "video", "video_note", "voice"]

	cmds = [
		"start", "stop", "users", "info", "motd", "toggledebug", "togglekarma",
		"version", "source", "modhelp", "adminhelp", "modsay", "adminsay", "mod",
		"admin", "warn", "delete", "uncooldown", "blacklist", "s", "sign",
		"tripcode", "settripcode", "t", "tsign"
	]
	for c in cmds: # maps /<c> to the function cmd_<c>
		c = c.lower()
		registered_commands[c] = globals()["cmd_" + c]
	handler(relay, content_types=types)

def handler(func, *args, **kwargs):
	def wrapper(*args, **kwargs):
		try:
			func(*args, **kwargs)
		except Exception as e:
			logging.exception("Exception raised in event handler")
	bot.message_handler(*args, **kwargs)(wrapper)

def run():
	while True:
		try:
			bot.polling(none_stop=True)
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

class UserContainer():
	def __init__(self, u):
		self.id = u.id
		self.username = u.username
		self.realname = u.first_name
		if u.last_name is not None:
			self.realname += " " + u.last_name

def takesArgument(optional=False):
	def f(func):
		def wrap(ev):
			arg = ""
			if " " in ev.text:
				arg = ev.text[ev.text.find(" ")+1:].strip()
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

# Message sending (queue-related)

class QueueItem():
	__slots__ = ("user_id", "msid", "func")
	def __init__(self, user, msid, func):
		self.user_id = None
		if user is not None:
			self.user_id = user.id
		self.msid = msid
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

def resend_message(chat_id, ev, reply_to=None):
	if (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None):
		# forward message instead of re-sending the contents
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	kwargs = {}
	if reply_to is not None:
		kwargs["reply_to_message_id"] = reply_to

	# re-send message based on content type
	if ev.content_type == "text":
		pass
	elif ev.content_type == "photo":
		photo = sorted(ev.photo, key=lambda e: e.width*e.height, reverse=True)[0]
		return bot.send_photo(chat_id, photo.file_id, caption=ev.caption, **kwargs)
	elif ev.content_type == "audio":
		for prop in ["performer", "title"]:
			kwargs[prop] = getattr(ev.audio, prop)
		return bot.send_audio(chat_id, ev.audio.file_id, caption=ev.caption, **kwargs)
	elif ev.content_type == "document":
		return bot.send_document(chat_id, ev.document.file_id, caption=ev.caption, **kwargs)
	elif ev.content_type == "video":
		return bot.send_video(chat_id, ev.video.file_id, caption=ev.caption, **kwargs)
	elif ev.content_type == "voice":
		return bot.send_voice(chat_id, ev.voice.file_id, caption=ev.caption, **kwargs)
	elif ev.content_type == "video_note":
		return bot.send_video_note(chat_id, ev.video_note.file_id, **kwargs)
	elif ev.content_type == "location":
		for prop in ["latitude", "longitude"]:
			kwargs[prop] = getattr(ev.location, prop)
		return bot.send_location(chat_id, **kwargs)
	elif ev.content_type == "venue":
		kwargs["latitude"] = ev.venue.location.latitude
		kwargs["longitude"] = ev.venue.location.longitude
		for prop in ["title", "address", "foursquare_id"]:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_venue(chat_id, **kwargs)
	elif ev.content_type == "contact":
		for prop in ["phone_number", "first_name", "last_name"]:
			kwargs[prop] = getattr(ev.contact, prop)
		return bot.send_contact(chat_id, **kwargs)
	elif ev.content_type == "sticker":
		return bot.send_sticker(chat_id, ev.sticker.file_id, **kwargs)
	else:
		raise NotImplementedError("content_type = %s" % ev.content_type)

	# for text
	s = ev.text
	if ev.entities is not None:
		for ent in ev.entities:
			if ent.type == "text_link":
				s += "\n(%s)" % ent.url
	return bot.send_message(chat_id, s, **kwargs)

def send_to_single_inner(chat_id, ev, reply_to=None):
	if isinstance(ev, rp.Reply):
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
		if ev.type == rp.types.CUSTOM:
			kwargs2["disable_web_page_preview"] = True
		return bot.send_message(chat_id, rp.formatForTelegram(ev), parse_mode="HTML", **kwargs2)
	else:
		return resend_message(chat_id, ev, reply_to=reply_to)

def send_to_single(ev, msid, user, reply_msid):
	# set reply_to_message_id if applicable
	reply_to = None
	if reply_msid is not None:
		reply_to = ch.lookupMapping(user.id, msid=reply_msid)

	def f(ev=ev, msid=msid, user=user):
		while True:
			try:
				ev2 = send_to_single_inner(user.id, ev, reply_to=reply_to)
			except telebot.apihelper.ApiException as e:
				retry = check_telegram_exc(e, user)
				if retry:
					continue
				return
			break
		ch.saveMapping(user.id, msid, ev2.message_id)
	put_into_queue(user, msid, f)

def check_telegram_exc(e, user):
	errmsgs = ["bot was blocked by the user", "user is deactivated", "PEER_ID_INVALID"]
	if any(msg in e.result.text for msg in errmsgs):
		if user is not None:
			logging.warning("Force leaving %s because bot is blocked", user)
			core.force_user_leave(user)
		return False

	if "Too Many Requests" in e.result.text:
		d = json.loads(e.result.text)["parameters"]["retry_after"]
		d = min(d, 30) # supposedly this is in seconds, but you sometimes get 100 or even 2000
		logging.warning("API rate limit hit, waiting for %ds", d)
		time.sleep(d)
		return True # retry

	logging.exception("API exception")
	return False

####

# Event receiver: handles all things the core decides to do "on its own":
# e.g. karma notifications, deletion of messages, signed messages
# This does *not* include direct replies to commands or relaying messages.

@core.registerReceiver
class MyReceiver(core.Receiver):
	@staticmethod
	def reply(m, msid, who, except_who, reply_msid):
		if who is not None:
			return send_to_single(m, msid, who, reply_msid)

		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who and not user.debugEnabled:
				continue
			send_to_single(m, msid, user, reply_msid)
	@staticmethod
	def delete(msid):
		tmp = ch.getMessage(msid)
		except_id = None if tmp is None else tmp.user_id
		message_queue.delete(lambda item, msid=msid: item.msid == msid)
		# FIXME: there's a hard to avoid race condition with currently being sent messages here
		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user.id == except_id:
				continue

			id = ch.lookupMapping(user.id, msid=msid)
			if id is None:
				continue
			def f(user=user, id=id):
				bot.delete_message(user.id, id)
			# queued message has msid=None here since this is a deletion, not a message being sent
			put_into_queue(user, None, f)
	@staticmethod
	def stop_invoked(user, delete_out):
		message_queue.delete(lambda item, user_id=user.id: item.user_id == user_id)
		if not delete_out:
			return
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

cmd_settripcode = cmd_tripcode # legacy alias


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

def cmd_warn(ev, delete=False):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	send_answer(ev, core.warn_user(c_user, reply_msid, delete), True)

cmd_delete = lambda ev: cmd_warn(ev, True)

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

def cmd_plusone(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), True)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), True)
	return send_answer(ev, core.give_karma(c_user, reply_msid), True)


def relay(ev):
	# handle commands and karma giving
	if ev.content_type == "text" and ev.text.startswith("/"):
		pos = ev.text.find(" ") if " " in ev.text else len(ev.text)
		c = ev.text[1:pos].lower()
		if c in registered_commands.keys():
			registered_commands[c](ev)
		return
	elif ev.content_type == "text" and ev.text.strip() == "+1":
		return cmd_plusone(ev)

	# filter disallowed media types
	if not allow_documents and ev.content_type == "document" and ev.document.mime_type not in ("image/gif", "video/mp4"):
		return

	is_media = (ev.forward_from is not None or
		ev.forward_from_chat is not None or
		ev.content_type in ("photo", "document", "video", "sticker"))
	msid = core.prepare_user_message(UserContainer(ev.from_user), calc_spam_score(ev), is_media)
	if msid is None or isinstance(msid, rp.Reply):
		return send_answer(ev, msid) # don't relay message, instead reply

	user = db.getUser(id=ev.from_user.id)

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

		send_to_single(ev, msid, user2, reply_msid)

@takesArgument()
def cmd_sign(ev, arg):
	c_user = UserContainer(ev.from_user)
	reply_msid = None
	if ev.reply_to_message is not None:
		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
		if reply_msid is None:
			logging.warning("Message replied to not found in cache")

	msid = core.send_signed_user_message(c_user, calc_spam_score(ev), arg, reply_msid)
	if isinstance(msid, rp.Reply):
		return send_answer(ev, msid, True)

	# save the original message in the mapping, this isn't done inside MyReceiver.reply()
	# since there's no "original message" at that point
	ch.saveMapping(c_user.id, msid, ev.message_id)

cmd_s = cmd_sign # alias

@takesArgument()
def cmd_tsign(ev, arg):
	c_user = UserContainer(ev.from_user)
	reply_msid = None
	if ev.reply_to_message is not None:
		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
		if reply_msid is None:
			logging.warning("Message replied to not found in cache")

	msid = core.send_signed_user_message(c_user, calc_spam_score(ev), arg, reply_msid, tripcode=True)
	if isinstance(msid, rp.Reply):
		return send_answer(ev, msid, True)

	ch.saveMapping(c_user.id, msid, ev.message_id)

cmd_t = cmd_tsign # alias
