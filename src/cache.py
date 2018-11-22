import logging
import itertools
from datetime import datetime, timedelta
from threading import Lock

from src.globals import *

class CachedMessage():
	def __init__(self, user_id=None):
		self.user_id = user_id # who has sent this message
		self.time = datetime.now() # when was this message seen?
		self.warned = False # was the user warned for this message?
		self.upvoted = set() # set of users that have given this message karma
	def isExpired(self):
		return datetime.now() >= self.time + timedelta(hours=24)
	def hasUpvoted(self, user):
		return user.id in self.upvoted
	def addUpvote(self, user):
		self.upvoted.add(user.id)

class Cache():
	def __init__(self):
		self.lock = Lock()
		self.counter = itertools.count()
		self.msgs = {} # dict(msid -> CachedMessage)
		self.idmap = {} # dict(uid -> dict(msid -> opaque))
	def _saveMapping(self, x, uid, msid, data):
		if uid not in x.keys():
			x[uid] = {}
		x[uid][msid] = data
	def _lookupMapping(self, x, uid, msid, data):
		if uid not in x.keys():
			return None
		if msid is not None:
			return x[uid].get(msid, None)
		# data is not None
		try:
			return next(msid for msid, _data in x[uid].items() if _data == data)
		except StopIteration as e:
			return None

	def assignMessageId(self, cm):
		with self.lock:
			ret = next(self.counter)
			self.msgs[ret] = cm
		return ret
	def getMessage(self, msid):
		with self.lock:
			return self.msgs.get(msid, None)
	def saveMapping(self, uid, msid, data):
		with self.lock:
			self._saveMapping(self.idmap, uid, msid, data)
	def lookupMapping(self, uid, msid=None, data=None):
		if msid is None and data is None:
			raise ValueError()
		with self.lock:
			return self._lookupMapping(self.idmap, uid, msid, data)
	def expire(self):
		ids = set()
		with self.lock:
			for msid in list(self.msgs.keys()):
				if not self.msgs[msid].isExpired():
					continue
				ids.add(msid)
				del self.msgs[msid] # delete from primary cache
				for d in self.idmap.values(): # delete from id mapping
					d.pop(msid, None)
		if len(ids) > 0:
			logging.debug("Expired %d entries from cache", len(ids))
		return ids
