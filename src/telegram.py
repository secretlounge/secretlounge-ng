import telebot
import logging
import time
import re
from queue import PriorityQueue

import src.core as core
import src.replies as rp
from src.globals import *

bot = None
db = None
ch = None
message_queue = None

allow_documents = True

def init(config, _db, _ch):
	global bot, db, ch, message_queue
	if config["bot_token"] == "":
		logging.error("No telegram token specified.")
		exit(1)

	logging.getLogger("urllib3").setLevel(logging.WARNING) # very noisy with debug otherwise
	bot = telebot.TeleBot(config["bot_token"], threaded=False)
	db = _db
	ch = _ch
	message_queue = PriorityQueue()

	allow_contacts = config["allow_contacts"]
	allow_documents = config["allow_documents"]

	types = ["text", "location", "venue", "game"]
	if allow_contacts:
		types += ["contact"]
	types += ["audio", "document", "photo", "sticker", "video", "video_note", "voice"]

	cmds = [
		"start", "stop", "users", "info", "motd", "version",
		"modhelp", "adminhelp", "modsay", "adminsay", "mod",
		"admin", "warn", "delete", "blacklist"
	]
	for c in cmds: # maps /Abc to the function cmd_abc
		handler(globals()["cmd_" + c.lower()], commands=[c])
	handler(cmd_toggledebug, commands=["toggleDebug", "toggledebug"]) # FIXME: proper case-insensitiveness
	handler(cmd_togglekarma, commands=["toggleKarma", "togglekarma"])
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

class QueueItem():
	def __init__(self, user, func):
		if user is None:
			self.prio = 2**32 # very very very low priority
		else:
			self.prio = user.getMessagePriority()
		self.func = func
	def __lt__(self, other):
		return self.prio < other.prio
	def __le__(self, other):
		return self.prio <= other.prio
	def __eq__(self, other):
		return self.prio == other.prio
	def __gt__(self, other):
		return self.prio > other.prio
	def __ge__(self, other):
		return self.prio >= other.prio
	def call(self):
		try:
			self.func()
		except Exception as e:
			logging.exception("Exception raised during queued message")

def send_thread():
	while True:
		item = message_queue.get()
		item.call()
		message_queue.task_done()

class UserContainer():
	def __init__(self, u):
		self.id = u.id
		self.username = u.username
		self.realname = u.first_name
		if u.last_name is not None:
			self.realname += " " + u.last_name

def wrap_core(func, reply_to=False):
	def f(ev):
		m = func(UserContainer(ev.from_user))
		send_answer(ev, m, reply_to=reply_to)
	return f

def send_answer(ev, m, reply_to=False):
	if m is None:
		return
	if type(m) == list:
		for m2 in m:
			send_answer(ev, m2)
	else:
		kwargs = {"reply_to_message_id": ev.message_id} if reply_to else {}
		def f():
			bot.send_message(ev.chat.id, rp.formatForTelegram(m), parse_mode="HTML", **kwargs)
		try:
			user = db.getUser(id=ev.from_user.id)
		except KeyError as e:
			user = None # happens on e.g. /start
		message_queue.put(QueueItem(user, f))

def resend_message(chat_id, ev, reply_to=None):
	if ev.forward_from is not None or ev.forward_from_chat is not None:
		# forward message instead of re-sending the contents
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	kwargs = {}
	if reply_to is not None:
		kwargs["reply_to_message_id"] = reply_to

	# re-send message based on content type
	if ev.content_type == "_internal_reply": # hack!!
		return bot.send_message(chat_id, rp.formatForTelegram(ev._reply), parse_mode="HTML", **kwargs)
	elif ev.content_type == "text":
		return bot.send_message(chat_id, ev.text, **kwargs)
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
		for prop in ["latitude", "longitude", "title", "address", "foursquare_id"]:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_venue(chat_id, **kwargs)
	elif ev.content_type == "contact":
		for prop in ["phone_number", "first_name", "last_name"]:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_contact(chat_id, **kwargs)
	elif ev.content_type == "game":
		return bot.send_game(chat_id, ev.game.title, **kwargs) # ???
	elif ev.content_type == "sticker":
		return bot.send_sticker(chat_id, ev.sticker.file_id, **kwargs)
	else:
		raise NotImplementedError("content_type = %s" % ev.content_type)

def wrap_in_ev(m):
	# FIXME: this is a terrible hack
	ev = telebot.types.Message(None, None, None, None, "_internal_reply", [])
	setattr(ev, "_reply", m)
	return ev

def calc_spam_score(ev):
	if ev.content_type == "sticker":
		return SCORE_STICKER
	elif ev.content_type == "text":
		pass
	else:
		return SCORE_MESSAGE
	s = SCORE_MESSAGE + len(ev.text) * SCORE_CHARACTER
	regex = re.compile(r"(https?:\/\/|\b)[a-z0-9-_]+\.[a-z]{2,}", flags=re.I)
	if re.search(regex, ev.text) is not None:
		s += len(re.findall(regex, ev.text)) * SCORE_LINK
	return s

####

def send_to_single(ev, msid, user, reply_msid):
	# set reply_to_message_id if applicable
	kwargs = {}
	if reply_msid is not None:
		kwargs["reply_to"] = ch.lookupMapping(user.id, msid=reply_msid)

	errmsgs = [b"bot was blocked by the user", b"user is deactivated", b"PEER_ID_INVALID"]

	def f(ev=ev, msid=msid, user=user):
		try:
			ev2 = resend_message(user.id, ev, **kwargs)
		except telebot.apihelper.ApiException as e:
			if any(msg in e.result.text for msg in errmsgs):
				logging.warning("Force leaving %s because bot is blocked", user)
				with db.modifyUser(id=user.id) as user:
					user.setLeft()
			else:
				logging.exception("Message send failed for user %s", user)
			return
		except Exception as e:
			logging.exception("Message send failed for user %s", user)
			return
		ch.saveMapping(user.id, msid, ev2.message_id)
	message_queue.put(QueueItem(user, f))

@core.registerReceiver
class MyReceiver(core.Receiver):
	@staticmethod
	def push_reply(m, msid, who, except_who, reply_msid):
		logging.debug("push_reply(m.type=%s, msid=%d)", rp.types.reverse[m.type], msid)
		ev = wrap_in_ev(m)
		if who is not None:
			return send_to_single(ev, msid, who, reply_msid)

		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who and not user.debugEnabled:
				continue
			send_to_single(ev, msid, user, reply_msid)
	@staticmethod
	def push_delete(msid):
		logging.debug("push_delete(msid=%d)", msid)
		tmp = ch.getMessage(msid)
		except_who = None if tmp is None else tmp.user_id
		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who:
				continue
			# FIXME: we don't have a way to abort messages that are currently in queue
			id = ch.lookupMapping(user.id, msid=msid)
			if id is None:
				continue
			def f(user=user, id=id):
				bot.delete_message(user.id, id)
			message_queue.put(QueueItem(user, f))

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

def cmd_motd(ev):
	c_user = UserContainer(ev.from_user)
	arg = ""
	if " " in ev.text:
		arg = ev.text[ev.text.find(" ")+1:].strip()

	if arg == "":
		send_answer(ev, core.get_motd(c_user), reply_to=True)
	else:
		send_answer(ev, core.set_motd(c_user, arg), reply_to=True)

cmd_toggledebug = wrap_core(core.toggle_debug)
cmd_togglekarma = wrap_core(core.toggle_karma)


def cmd_modhelp(ev):
	send_answer(ev, rp.Reply(rp.types.HELP_MODERATOR), True)

def cmd_adminhelp(ev):
	send_answer(ev, rp.Reply(rp.types.HELP_ADMIN), True)

def cmd_version(ev):
	send_answer(ev, rp.Reply(rp.types.PROGRAM_VERSION, version=VERSION), True)


def cmd_modsay(ev):
	c_user = UserContainer(ev.from_user)
	if " " not in ev.text:
		return
	arg = ev.text[ev.text.find(" ")+1:].strip()

	return send_answer(ev, core.send_mod_message(c_user, arg), True)

def cmd_adminsay(ev):
	c_user = UserContainer(ev.from_user)
	if " " not in ev.text:
		returnindex
	arg = ev.text[ev.text.find(" ")+1:].strip()

	return send_answer(ev, core.send_admin_message(c_user, arg), True)

def cmd_mod(ev):
	c_user = UserContainer(ev.from_user)
	if " " not in ev.text:
		return
	arg = ev.text[ev.text.find(" ")+1:].strip().lstrip("@")
	send_answer(ev, core.promote_user(c_user, arg, RANKS.mod), True)

def cmd_admin(ev):
	c_user = UserContainer(ev.from_user)
	if " " not in ev.text:
		return
	arg = ev.text[ev.text.find(" ")+1:].strip().lstrip("@")
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

def cmd_blacklist(ev):
	c_user = UserContainer(ev.from_user)
	arg = ""
	if " " in ev.text:
		arg = ev.text[ev.text.find(" ")+1:].strip()

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
	if ev.content_type == "text" and ev.text.startswith("/"):
		return # drop unknown commands
	elif ev.content_type == "text" and ev.text.strip() == "+1":
		return cmd_plusone(ev)
	elif not allow_documents and ev.content_type == "document" and ev.document.mime_type not in ("image/gif", "video/mp4"):
		return

	msid = core.prepare_user_message(UserContainer(ev.from_user), calc_spam_score(ev))
	if type(msid) == rp.Reply: # don't relay message, instead reply with something
		return send_answer(ev, msid)

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
