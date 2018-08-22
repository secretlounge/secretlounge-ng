import itertools
import time
import logging
from queue import PriorityQueue
from threading import Lock
from datetime import timedelta
from util.tripcrypt import _crypt as crypt

class Scheduler():
	def __init__(self):
		self.tasks = [] # list of [interval, next_trigger, func]
	@staticmethod
	def _wrapped_call(f):
		try:
			f()
		except Exception as e:
			logging.exception("Exception raised during scheduled task")
	def register(self, func, **kwargs):
		interval = timedelta(**kwargs) // timedelta(seconds=1)
		self.tasks.append([interval, 0, func])
	def run(self):
		while True:
			# Run tasks that have expired
			now = int(time.monotonic())
			for e in self.tasks:
				if now >= e[1]:
					Scheduler._wrapped_call(e[2])
					e[1] = now + e[0]
			# Wait until a task expires
			now = int(time.monotonic())
			wait = min((e[1] - now) for e in self.tasks)
			if wait > 0:
				time.sleep(wait)

class MutablePriorityQueue():
	def __init__(self):
		self.queue = PriorityQueue() # contains (prio, iid)
		self.items = {} # maps iid -> opaque
		self.counter = itertools.count()
		# protects `items` and `counter`, `queue` has its own lock
		self.lock = Lock()
	def get(self):
		while True:
			_, iid = self.queue.get()
			with self.lock:
				# skip deleted entries
				if iid in self.items.keys():
					return self.items.pop(iid)
	def put(self, prio, data):
		with self.lock:
			iid = next(self.counter)
			self.items[iid] = data
		self.queue.put((prio, iid))
	def delete(self, selector):
		with self.lock:
			keys = list(self.items.keys())
			for iid in keys:
				if selector(self.items[iid]):
					del self.items[iid]

class Enum():
	def __init__(self, m, reverse=True):
		assert len(set(m.values())) == len(m)
		self._m = m
		if reverse:
			self.reverse = Enum({v: k for k, v in m.items()}, reverse=False)
	def __getitem__(self, key):
		return self._m[key]
	def __getattr__(self, key):
		return self[key]
	def keys(self):
		return self._m.keys()
	def values(self):
		return self._m.values()

def gen_tripcode(tripcode):
	# doesn't actually match 4chan's algorithm exactly
	pos = tripcode.find("#")
	trname = tripcode[:pos]
	trpass = tripcode[pos+1:]
	trpass = trpass.encode("sjis","xmlcharrefreplace")
	
	salt = (trpass[:8] + b"H..")[1:3]
	salt = salt.translate(b'................................'
						b'.............../0123456789ABCDEF'
						b'GABCDEFGHIJKLMNOPQRSTUVWXYZabcde'
						b'fabcdefghijklmnopqrstuvwxyz.....'
						b'................................'
						b'................................'
						b'................................'
						b'................................')

	trip_final = crypt(trpass[:8], salt.decode("utf8"))

	return trname + " !" + trip_final[3:]
