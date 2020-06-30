import logging
from datetime import datetime, timedelta
from threading import Lock
from importlib import import_module
from typing import Optional, Dict

from . import replies as rp
from .globals import *
from .database import User, SystemConfig
from .cache import CachedMessage
from .util import genTripcode

# module variables

db = None
ch = None
spam_scores = None
sign_last_used: Dict[int, datetime] = {}

# settings

blacklist_contact: str = None
enable_signing: bool = None
allow_remove_command: bool = None
media_limit_period: Optional[timedelta] = None
sign_interval: timedelta = None

class IUserContainer():
	id: int
	username: str
	realname: str
	def __init__(self):
		raise NotImplementedError()

def init(config: dict, _db, _ch):
	global db, ch, spam_scores, blacklist_contact, enable_signing, allow_remove_command, media_limit_period, sign_interval
	db = _db
	ch = _ch
	spam_scores = ScoreKeeper()

	blacklist_contact = config.get("blacklist_contact", "")
	enable_signing = config["enable_signing"]
	allow_remove_command = config["allow_remove_command"]
	if "media_limit_period" in config.keys():
		media_limit_period = timedelta(hours=int(config["media_limit_period"]))
	sign_interval = timedelta(seconds=int(config.get("sign_limit_interval", 600)))

	if config.get("locale"):
		rp.localization = import_module("..replies_" + config["locale"], __name__).localization

	if config.get("secret_salt"):
		User.setSalt(bytes.fromhex(config["secret_salt"]))

	# initialize db if empty
	if db.getSystemConfig() is None:
		c = SystemConfig()
		c.defaults()
		db.setSystemConfig(c)

def register_tasks(sched):
	# spam score handling
	sched.register(spam_scores.scheduledTask, seconds=SPAM_INTERVAL_SECONDS)
	# warning removal
	def task():
		now = datetime.now()
		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user.warnExpiry is not None and now >= user.warnExpiry:
				with db.modifyUser(id=user.id) as user:
					user.removeWarning()
	sched.register(task, minutes=15)

s_active_users = {}
def active_user_stat():
	d = {
		"active_users_15m": timedelta(minutes=15),
		"active_users_2h": timedelta(hours=2),
		"active_users_12h": timedelta(hours=12),
	}
	res = {key: 0 for key in d.keys()}
	now = datetime.now()
	for last_active in s_active_users.values():
		for key, delta in d.items():
			res[key] += 1 if (now - last_active <= delta) else 0
	return res
stats.register_source(active_user_stat)

def updateUserFromEvent(user, c_user: IUserContainer):
	user.username = c_user.username
	user.realname = c_user.realname
	user.lastActive = datetime.now()
	if user.isJoined():
		s_active_users[user.id] = datetime.now()

def getUserByName(username):
	username = username.lstrip("@").lower()
	# there *should* only be a single joined user with a given username
	for user in db.iterateUsers():
		if not user.isJoined():
			continue
		if user.username is not None and user.username.lower() == username:
			return user
	return None

def getUserByOid(oid):
	for user in db.iterateUsers():
		if not user.isJoined():
			continue
		if user.getObfuscatedId() == oid:
			return user
	return None

def requireUser(func):
	def wrapper(c_user, *args, **kwargs):
		if isinstance(c_user, User):
			user = c_user
		else:
			# fetch user from db
			try:
				user = db.getUser(id=c_user.id)
			except KeyError as e:
				return rp.Reply(rp.types.USER_NOT_IN_CHAT)

		# keep db entry up to date
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)

		# check for blacklist or absence
		if user.isBlacklisted():
			return rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		elif not user.isJoined():
			return rp.Reply(rp.types.USER_NOT_IN_CHAT)

		# call original function
		return func(user, *args, **kwargs)
	return wrapper

def requireRank(need_rank):
	def f(func):
		def wrapper(user, *args, **kwargs):
			assert isinstance(user, User), "you fucked up the decorator order"
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
				return s + n <= SPAM_LIMIT_HIT
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
	@staticmethod
	def reply(m: rp.Reply, msid: int, who, except_who, reply_to: bool):
		raise NotImplementedError()
	@staticmethod
	def delete(msids: 'list[int]'):
		raise NotImplementedError()
	@staticmethod
	def stop_invoked(who, delete_out: bool):
		raise NotImplementedError()

class Sender(Receiver): # flawless class hierarchy I know...
	receivers = []
	@staticmethod
	def reply(m, msid, who, except_who, reply_to):
		logging.debug("reply(m.type=%s, msid=%r, reply_to=%r)", rp.types.reverse[m.type], msid, reply_to)
		for r in Sender.receivers:
			r.reply(m, msid, who, except_who, reply_to)
	@staticmethod
	def delete(msids):
		logging.debug("delete(msids=%r)", msids)
		for r in Sender.receivers:
			r.delete(msids)
	@staticmethod
	def stop_invoked(who, delete_out=False):
		logging.debug("stop_invoked(who=%s)", who)
		for r in Sender.receivers:
			r.stop_invoked(who, delete_out)

def registerReceiver(obj):
	assert issubclass(obj, Receiver)
	Sender.receivers.append(obj)
	return obj

####

def user_join(c_user: IUserContainer):
	try:
		user = db.getUser(id=c_user.id)
	except KeyError as e:
		user = None

	if user is not None:
		# check if user can't rejoin
		err = None
		if user.isBlacklisted():
			err = rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		elif user.isJoined():
			err = rp.Reply(rp.types.USER_IN_CHAT)
		if err is not None:
			with db.modifyUser(id=user.id) as user:
				updateUserFromEvent(user, c_user)
			return err

		# user rejoins
		absenceTime = datetime.now() - user.left
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)
			user.setLeft(False)
		logging.info("%s rejoined chat", user)
		ret = [rp.Reply(rp.types.CHAT_JOIN)]

		motd = db.getSystemConfig().motd
		if motd and absenceTime / timedelta(days=1) >= MOTD_REMIND_DAYS:
			ret.append(rp.Reply(rp.types.CUSTOM, text=motd))

		return ret

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
	if motd:
		ret.append(rp.Reply(rp.types.CUSTOM, text=motd))

	return ret

def force_user_leave(user_id, blocked=True):
	with db.modifyUser(id=user_id) as user:
		user.setLeft()
	if blocked:
		logging.warning("Force leaving %s because bot is blocked", user)
	Sender.stop_invoked(user)

@requireUser
def user_leave(user: User):
	force_user_leave(user.id, blocked=False)
	logging.info("%s left chat", user)

	return rp.Reply(rp.types.CHAT_LEAVE)

@requireUser
def get_info(user: User):
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
def get_info_mod(user: User, msid):
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
def get_users(user: User):
	if user.rank < RANKS.mod:
		n = sum(1 for user2 in db.iterateUsers() if user2.isJoined())
		return rp.Reply(rp.types.USERS_INFO, count=n)
	active, inactive, black = 0, 0, 0
	for user2 in db.iterateUsers():
		if user2.isBlacklisted():
			black += 1
		elif not user2.isJoined():
			inactive += 1
		else:
			active += 1
	return rp.Reply(rp.types.USERS_INFO_EXTENDED,
		active=active, inactive=inactive, blacklisted=black,
		total=active + inactive + black)

@requireUser
def get_system_text(user: User, key: str):
	if key not in ("motd", "privacy"):
		raise ValueError()
	v = getattr(db.getSystemConfig(), key)
	if v:
		return rp.Reply(rp.types.CUSTOM, text=v)

@requireUser
@requireRank(RANKS.admin)
def set_system_text(user: User, key: str, arg: str):
	if key not in ("motd", "privacy"):
		raise ValueError()
	with db.modifySystemConfig() as config:
		setattr(config, key, arg)
	logging.info("%s set %s to: %r", user, key, arg)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
def toggle_debug(user: User):
	with db.modifyUser(id=user.id) as user:
		user.debugEnabled = not user.debugEnabled
		new = user.debugEnabled
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Debug mode", enabled=new)

@requireUser
def toggle_karma(user: User):
	with db.modifyUser(id=user.id) as user:
		user.hideKarma = not user.hideKarma
		new = user.hideKarma
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Karma notifications", enabled=not new)

@requireUser
def get_tripcode(user: User):
	if not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	return rp.Reply(rp.types.TRIPCODE_INFO, tripcode=user.tripcode)

@requireUser
def set_tripcode(user: User, text: str):
	if not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	if not 0 < text.find("#") < len(text) - 1:
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)
	if "\n" in text or len(text) > 30:
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)

	with db.modifyUser(id=user.id) as user:
		user.tripcode = text
	tripname, tripcode = genTripcode(user.tripcode)
	return rp.Reply(rp.types.TRIPCODE_SET, tripname=tripname, tripcode=tripcode)

@requireUser
@requireRank(RANKS.admin)
def promote_user(user: User, username2: str, rank: int):
	user2 = getUserByName(username2)
	if user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank >= rank:
		return
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
def send_mod_message(user: User, arg: str):
	text = arg + " ~<b>mods</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent mod message: %s", user, arg)

@requireUser
@requireRank(RANKS.admin)
def send_admin_message(user: User, arg: str):
	text = arg + " ~<b>admins</b>"
	m = rp.Reply(rp.types.CUSTOM, text=text)
	_push_system_message(m)
	logging.info("%s sent admin message: %s", user, arg)

s_warnings_given = stats.countable_source("warnings_given")
@requireUser
@requireRank(RANKS.mod)
def warn_user(user: User, msid, delete=False):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	if not cm.warned:
		with db.modifyUser(id=cm.user_id) as user2:
			d = user2.addWarning()
			user2.karma -= KARMA_WARN_PENALTY
		_push_system_message(
			rp.Reply(rp.types.GIVEN_COOLDOWN, duration=d, deleted=delete),
			who=user2, reply_to=msid)
		cm.warned = True
	else:
		user2 = db.getUser(id=cm.user_id)
		if not delete: # allow deleting already warned messages
			return rp.Reply(rp.types.ERR_ALREADY_WARNED)
	if delete:
		Sender.delete([msid])
	logging.info("%s warned [%s]%s", user, user2.getObfuscatedId(), delete and " (message deleted)" or "")
	s_warnings_given(1)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.mod)
def delete_message(user: User, msid):
	if not allow_remove_command:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)
	_push_system_message(rp.Reply(rp.types.MESSAGE_DELETED), who=user2, reply_to=msid)
	Sender.delete([msid])
	logging.info("%s deleted a message from [%s]", user, user2.getObfuscatedId())
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def cleanup_messages(user: User):
	msids = []
	def f(msid: int, cm: CachedMessage):
		if cm.user_id is None:
			return
		if 1337 in cm.upvoted: # mark that we've been here before
			return
		user2 = db.getUser(id=cm.user_id)
		if user2.isBlacklisted():
			msids.append(msid)
			cm.upvoted.add(1337)
	ch.iterateMessages(f)
	logging.info("%s invoked cleanup (matched: %d)", user, len(msids))
	Sender.delete(msids)
	return rp.Reply(rp.types.DELETION_QUEUED, count=len(msids))

@requireUser
@requireRank(RANKS.admin)
def uncooldown_user(user: User, oid2=None, username2=None):
	if oid2 is not None:
		user2 = getUserByOid(oid2)
		if user2 is None:
			return rp.Reply(rp.types.ERR_NO_USER_BY_ID)
	elif username2 is not None:
		user2 = getUserByName(username2)
		if user2 is None:
			return rp.Reply(rp.types.ERR_NO_USER)
	else:
		raise ValueError()

	if not user2.isInCooldown():
		return rp.Reply(rp.types.ERR_NOT_IN_COOLDOWN)
	with db.modifyUser(id=user2.id) as user2:
		user2.removeWarning()
		was_until = user2.cooldownUntil
		user2.cooldownUntil = None
	logging.info("%s removed cooldown from %s (was until %s)", user, user2, format_datetime(was_until))
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def blacklist_user(user: User, msid, reason: str):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	with db.modifyUser(id=cm.user_id) as user2:
		if user2.rank >= user.rank:
			return
		user2.setBlacklisted(reason)
	cm.warned = True
	Sender.stop_invoked(user2, True) # do this before queueing new messages below
	_push_system_message(
		rp.Reply(rp.types.ERR_BLACKLISTED, reason=reason, contact=blacklist_contact),
		who=user2, reply_to=msid)
	Sender.delete([msid])
	logging.info("%s was blacklisted by %s for: %s", user2, user, reason)
	return rp.Reply(rp.types.SUCCESS)

s_karma_given = stats.countable_source("karma_given")
@requireUser
def give_karma(user: User, msid):
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
	s_karma_given(1)
	if not user2.hideKarma:
		_push_system_message(rp.Reply(rp.types.KARMA_NOTIFICATION), who=user2, reply_to=msid)
	return rp.Reply(rp.types.KARMA_THANK_YOU)


@requireUser
def prepare_user_message(user: User, msg_score: int, *, is_media=False, signed=False, tripcode=False):
	# prerequisites
	if user.isInCooldown():
		return rp.Reply(rp.types.ERR_COOLDOWN, until=user.cooldownUntil)
	if (signed or tripcode) and not enable_signing:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)
	if tripcode and user.tripcode is None:
		return rp.Reply(rp.types.ERR_NO_TRIPCODE)
	if is_media and user.rank < RANKS.mod and media_limit_period is not None:
		if (datetime.now() - user.joined) < media_limit_period:
			return rp.Reply(rp.types.ERR_MEDIA_LIMIT)

	ok = spam_scores.increaseSpamScore(user.id, msg_score)
	if not ok:
		return rp.Reply(rp.types.ERR_SPAMMY)

	# enforce signing cooldown
	if signed and sign_interval.total_seconds() > 1:
		last_used = sign_last_used.get(user.id, None)
		if last_used and (datetime.now() - last_used) < sign_interval:
			return rp.Reply(rp.types.ERR_SPAMMY_SIGN)
		sign_last_used[user.id] = datetime.now()

	return ch.assignMessageId(CachedMessage(user.id))

# who is None -> to everyone except the user <except_who> (if applicable)
# who is not None -> only to the user <who>
# reply_to: msid the message is in reply to
def _push_system_message(m, *, who=None, except_who=None, reply_to=None):
	msid = None
	if who is None: # we only need an ID if multiple people can see the msg
		msid = ch.assignMessageId(CachedMessage())
	Sender.reply(m, msid, who, except_who, reply_to)
