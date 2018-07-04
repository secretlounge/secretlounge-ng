import logging
import time
from datetime import datetime, timedelta
from threading import Lock

import src.replies as rp
from src.globals import *
from src.database import User, SystemConfig
from src.cache import CachedMessage

db = None
ch = None
spam_scores = None

def init(config, _db, _ch):
	global db, ch, spam_scores
	db = _db
	ch = _ch
	spam_scores = ScoreKeeper()

	if db.getSystemConfig() is None:
		c = SystemConfig()
		c.defaults()
		db.setSystemConfig(c)

def schedule_thread():
	next_warningcheck = datetime.utcfromtimestamp(0)
	while True:
		time.sleep(SPAM_INTERVAL_SECONDS)
		# decrease spam scores
		spam_scores.scheduledTask()
		# remove warnings
		now = datetime.now()
		if now >= next_warningcheck:
			for user in db.iterateUsers():
				if not user.isJoined():
					continue
				if user.warnExpiry is not None and now >= user.warnExpiry:
					with db.modifyUser(id=user.id) as user:
						user.removeWarning()
			next_warningcheck = datetime.now() + timedelta(minutes=15)

def updateUserFromEvent(user, c_user):
	user.username = c_user.username
	user.realname = c_user.realname
	user.lastActive = datetime.now()

def requireUser(func):
	def wrapper(c_user, *args, **kwargs):
		# fetch user from db
		try:
			user = db.getUser(id=c_user.id)
		except KeyError as e:
			return rp.Reply(rp.types.USER_NOT_IN_CHAT)
		# check for blacklist or absence
		if user.isBlacklisted():
			return rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason)
		elif not user.isJoined():
			return rp.Reply(rp.types.USER_NOT_IN_CHAT)
		# keep db entry up to date
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)
		# call original function
		return func(user, *args, **kwargs)
	return wrapper

def requireRank(need_rank):
	def f(func):
		def wrapper(user, *args, **kwargs):
			if type(user) != User:
				raise SyntaxError("you fucked up the decorator order")
			if user.rank < need_rank:
				return
			return func(user, *args, **kwargs)
		return wrapper
	return f

###

# RAM cache for spam scores

class ScoreKeeper():
	def __init__(self):
		self.lock = Lock()
		self.scores = {}
	def increaseSpamScore(self, uid, n):
		with self.lock:
			s = self.scores.get(uid, 0)
			if s > SPAM_LIMIT:
				return False
			elif s + n > SPAM_LIMIT:
				self.scores[uid] = SPAM_LIMIT_HIT
				return False
			self.scores[uid] = s + n
			return True
	def scheduledTask(self):
		with self.lock:
			for uid in list(self.scores.keys()):
				s = self.scores[uid] - 1
				if s <= 0:
					del self.scores[uid]
				else:
					self.scores[uid] = s

###

# Event receiver template and Sender class that fwds to all registered event receivers

class Receiver():
	def __init__(self):
		raise NotImplementedError()
	@staticmethod
	def reply(m, msid, who, except_who, reply_to):
		...
	@staticmethod
	def delete(msid):
		...
	@staticmethod
	def stop_invoked(who):
		...

class Sender(Receiver): # flawless class hierachy I know...
	receivers = []
	@staticmethod
	def reply(*args):
		for r in Sender.receivers:
			r.reply(*args)
	@staticmethod
	def delete(*args):
		for r in Sender.receivers:
			r.delete(*args)
	@staticmethod
	def stop_invoked(*args):
		for r in Sender.receivers:
			r.stop_invoked(*args)

def registerReceiver(obj):
	assert(issubclass(obj, Receiver))
	Sender.receivers.append(obj)
	return obj

####

def user_join(c_user):
	try:
		user = db.getUser(id=c_user.id)
	except KeyError as e:
		user = None

	if user is not None:
		if user.isJoined():
			return rp.Reply(rp.types.USER_IN_CHAT)
		# user rejoins
		with db.modifyUser(id=user.id) as user:
			user.setLeft(False)
		logging.info("%s rejoined chat", user)
		return rp.Reply(rp.types.CHAT_JOIN)

	# create new user
	user = User()
	user.defaults()
	user.id = c_user.id
	updateUserFromEvent(user, c_user)
	if not any(db.iterateUserIds()):
		user.rank = RANKS.admin

	logging.info("%s joined chat", user)
	db.addUser(user)
	ret = [rp.Reply(rp.types.CHAT_JOIN)]

	motd = db.getSystemConfig().motd
	if motd != "":
		ret.append(rp.Reply(rp.types.CUSTOM, text=motd))

	return ret

def force_user_leave(user):
	with db.modifyUser(id=user.id) as user:
		user.setLeft()
	Sender.stop_invoked(user)

@requireUser
def user_leave(user):
	force_user_leave(user)
	logging.info("%s left chat", user)

	return rp.Reply(rp.types.CHAT_LEAVE)

@requireUser
def get_info(user):
	params = {
		"id": user.getObfuscatedId(),
		"username": user.getFormattedName(),
		"rank_i": user.rank,
		"rank": RANKS.reverse[user.rank],
		"karma": user.karma,
		"warnings": user.warnings,
		"warnExpiry": user.warnExpiry,
		"cooldown": user.cooldownUntil if user.isInCooldown() else None,
	}
	return rp.Reply(rp.types.USER_INFO, **params)

@requireUser
@requireRank(RANKS.mod)
def get_info_mod(user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)
	params = {
		"id": user2.getObfuscatedId(),
		"karma": user2.getObfuscatedKarma(),
		"cooldown": user2.cooldownUntil if user2.isInCooldown() else None,
	}
	return rp.Reply(rp.types.USER_INFO_MOD, **params)

@requireUser
def get_users(user):
	if user.rank < RANKS.mod:
		n = sum(1 for user in db.iterateUsers() if user.isJoined())
		return rp.Reply(rp.types.USERS_INFO, count=n)
	active, inactive, black = 0, 0, 0
	for user in db.iterateUsers():
		if user.isBlacklisted():
			black += 1
		elif not user.isJoined():
			inactive += 1
		else:
			active += 1
	return rp.Reply(rp.types.USERS_INFO_EXTENDED,
		active=active, inactive=inactive, blacklisted=black,
		total=active + inactive + black)

@requireUser
def get_motd(user):
	motd = db.getSystemConfig().motd
	if motd == "": return
	return rp.Reply(rp.types.CUSTOM, text=motd)

@requireUser
@requireRank(RANKS.admin)
def set_motd(user, arg):
	with db.modifySystemConfig() as config:
		config.motd = arg
	return rp.Reply(rp.types.SUCCESS)

@requireUser
def toggle_debug(user):
	with db.modifyUser(id=user.id) as user:
		user.debugEnabled = not user.debugEnabled
		new = user.debugEnabled
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Debug mode", enabled=new)

@requireUser
def toggle_karma(user):
	with db.modifyUser(id=user.id) as user:
		user.hideKarma = not user.hideKarma
		new = user.hideKarma
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Karma notifications", enabled=not new)

@requireUser
@requireRank(RANKS.admin)
def promote_user(user, username2, rank):
	try:
		user2 = db.getUser(username=username2)
	except KeyError as e:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank >= rank: return
	with db.modifyUser(id=user2.id) as user2:
		user2.rank = rank
	if rank >= RANKS.admin:
		_push_system_message(rp.Reply(rp.types.PROMOTED_ADMIN), who=user2)
	elif rank >= RANKS.mod:
		_push_system_message(rp.Reply(rp.types.PROMOTED_MOD), who=user2)
	logging.info("%s was promoted by %s to: %d", user2, user, rank)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.mod)
def send_mod_message(user, arg):
	text = arg + " ~<b>mods</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent mod message: %s", user, arg)

@requireUser
@requireRank(RANKS.admin)
def send_admin_message(user, arg):
	text = arg + " ~<b>admins</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent admin message: %s", user, arg)

@requireUser
@requireRank(RANKS.mod)
def warn_user(user, msid, delete=False):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	if not cm.warned:
		with db.modifyUser(id=cm.user_id) as user2:
			d = user2.addWarning()
			user2.karma -= KARMA_WARN_PENALTY
		_push_system_message(rp.Reply(rp.types.GIVEN_COOLDOWN, duration=d, deleted=delete), who=user2, reply_to=msid)
		cm.warned = True
	else:
		user2 = db.getUser(id=cm.user_id)
		if not delete: # allow deleting already warned messages
			return rp.Reply(rp.types.ERR_ALREADY_WARNED)
	if delete:
		Sender.delete(msid)
	logging.info("%s warned [%s]%s", user, user2.getObfuscatedId(), delete and " (message deleted)" or "")
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def blacklist_user(user, msid, reason):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	with db.modifyUser(id=cm.user_id) as user2:
		if user2.rank >= user.rank: return
		user2.setBlacklisted(reason)
	_push_system_message(rp.Reply(rp.types.ERR_BLACKLISTED, reason=reason), who=user2, reply_to=msid)
	Sender.delete(msid)
	logging.info("%s was blacklisted by %s for: %s", user2, user, reason)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
def give_karma(user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	if cm.hasUpvoted(user):
		return rp.Reply(rp.types.ERR_ALREADY_UPVOTED)
	elif user.id == cm.user_id:
		return rp.Reply(rp.types.ERR_UPVOTE_OWN_MESSAGE)
	cm.addUpvote(user)
	user2 = db.getUser(id=cm.user_id)
	with db.modifyUser(id=cm.user_id) as user2:
		user2.karma += KARMA_PLUS_ONE
	if not user2.hideKarma:
		_push_system_message(rp.Reply(rp.types.KARMA_NOTIFICATION), who=user2, reply_to=msid)
	return rp.Reply(rp.types.KARMA_THANK_YOU)


@requireUser
def prepare_user_message(user, msg_score):
	if user.isInCooldown():
		return rp.Reply(rp.types.ERR_COOLDOWN, until=user.cooldownUntil)
	ok = spam_scores.increaseSpamScore(user.id, msg_score)
	if not ok:
		return rp.Reply(rp.types.ERR_SPAMMY)
	return ch.assignMessageId(CachedMessage(user.id))

# who is None -> to everyone except the user <except_who> (if applicable)
# who is not None -> only to the user <who>
# reply_to: msid the message is in reply to
def _push_system_message(m, who=None, except_who=None, reply_to=None):
	msid = None
	if who is None: # we only need an ID if multiple people can see the msg
		msid = ch.assignMessageId(CachedMessage())
	Sender.reply(m, msid, who, except_who, reply_to)
