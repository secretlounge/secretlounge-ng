import logging
import threading
from datetime import datetime, timedelta
from time import sleep
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
		self.lock = Lock() # only protects `msgs` and `idmap`
		self.msgs = {} # dict(msid -> CachedMessage)
		self.idmap = {} # dict(uid -> dict(msid -> opaque))
		self.msid = 0
	def assignMessageId(self, cm):
		ret = self.msid
		self.msid += 1
		with self.lock:
			self.msgs[ret] = cm
		return ret
	def getMessage(self, msid):
		with self.lock:
			return self.msgs.get(msid, None)
	def saveMapping(self, uid, msid, data):
		with self.lock:
			if uid not in self.idmap.keys():
				self.idmap[uid] = {}
			self.idmap[uid][msid] = data
	def lookupMapping(self, uid, msid=None, data=None):
		with self.lock:
			if uid not in self.idmap.keys():
				return None
			if msid is not None:
				return self.idmap[uid].get(msid, None)
			elif data is None:
				raise ValueError("no lookup criteria")
			try:
				return next(msid for msid, _data in self.idmap[uid].items() if _data == data)
			except StopIteration as e:
				return None
	def expire(self):
		n = 0
		with self.lock:
			for msid in list(self.msgs.keys()):
				if not self.msgs[msid].isExpired():
					continue
				n += 1
				del self.msgs[msid] # delete from primary cache
				for d in self.idmap.values(): # delete from id mapping
					d.pop(msid, None)
		if n > 0:
			logging.debug("Expired %d entries from cache", n)
		return n
	def expireThread(self):
		while True:
			sleep(6 * 60 * 60) # cache duration / 4
			self.expire()
