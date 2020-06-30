import logging
import itertools
from datetime import datetime, timedelta
from threading import RLock
from typing import Optional, Sequence, Set, Iterator, Dict

from .globals import *

class CachedMessage():
	__slots__ = ('user_id', 'time', 'warned', 'upvoted')
	user_id: Optional[int]
	time: datetime
	warned: bool
	upvoted: Set[int]
	def __init__(self, user_id=None):
		self.user_id = user_id # who has sent this message
		self.time = datetime.now() # when was this message created?
		self.warned = False # was the user warned for this message?
		self.upvoted = set() # user ids that have given this message karma
	def isExpired(self):
		return datetime.now() >= self.time + timedelta(hours=MESSAGE_EXPIRE_HOURS)
	def hasUpvoted(self, user):
		return user.id in self.upvoted
	def addUpvote(self, user):
		self.upvoted.add(user.id)

class Cache():
	lock: RLock
	counter: Iterator[int]
	msgs: Dict[int, CachedMessage]
	idmap: Dict[int, Dict[int, object]]
	def __init__(self):
		self.lock = RLock()
		self.counter = itertools.count()
		self.msgs = {} # dict(msid -> CachedMessage)
		self.idmap = {} # dict(uid -> dict(msid -> opaque))
		stats.register_source(lambda: {"cache_size": len(self.msgs)})

	def assignMessageId(self, cm: CachedMessage) -> int:
		with self.lock:
			ret = next(self.counter)
			self.msgs[ret] = cm
		return ret
	def getMessage(self, msid: int) -> CachedMessage:
		with self.lock:
			return self.msgs.get(msid, None)
	def iterateMessages(self, functor):
		with self.lock:
			for msid, cm in self.msgs.items():
				functor(msid, cm)

	# get user-specific mapping by key
	def getMapping(self, uid: int, msid: int) -> object:
		with self.lock:
			t = self.idmap.get(uid, None)
			if t is not None:
				return t.get(msid, None)
	# save user-specific mapping
	def saveMapping(self, uid: int, msid: int, data: object):
		with self.lock:
			if uid not in self.idmap.keys():
				self.idmap[uid] = {}
			self.idmap[uid][msid] = data
	# find user-specific mapping by value (linear search)
	def findMapping(self, uid: int, data: object) -> Optional[int]:
		with self.lock:
			t = self.idmap.get(uid, None)
			if t is not None:
				gen = (msid for msid, _data in t.items() if _data == data)
				return next(gen, None)
	# delete all user-specific mappings by key
	def deleteMappings(self, msid: int):
		with self.lock:
			for d in self.idmap.values():
				d.pop(msid, None)

	def expire(self) -> Sequence[int]:
		ids = set()
		with self.lock:
			for msid in list(self.msgs.keys()):
				if not self.msgs[msid].isExpired():
					continue
				ids.add(msid)
				# delete message itself and from mappings
				del self.msgs[msid]
				self.deleteMappings(msid)
		if len(ids) > 0:
			logging.debug("Expired %d entries from cache", len(ids))
		return ids
