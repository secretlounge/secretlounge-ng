# vim: set noet ts=4:
import itertools
import time
import logging
import json
import csv
from queue import PriorityQueue
from threading import Lock
from datetime import timedelta

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
def country_to_code(country):
	country = country.capitalize()
	dic = {}
	with open("wikipedia-iso-country-codes.csv") as f:
		file= csv.DictReader(f, delimiter=',')
		for line in file:
			dic[line['English short name lower case']] = line['Alpha-2 code']
	country_list = [country]
	try:
		country_code = [dic[x] for x in country_list]
	except KeyError:
		return None
	else:
		return country_code[0]


def code_to_country(code):
	code = code.upper()
	dic = {}
	with open("wikipedia-iso-country-codes.csv") as f:
		file= csv.DictReader(f, delimiter=',')
		for line in file:
			dic[line['English short name lower case']] = line['Alpha-2 code']
		country = [k for k,v in dic.items() if v == code]
		if country:
			return country
		else:
			return None

def langcode_to_flag(langcode):
	offset = 127397
	cc = langcode.upper()
	flag_list = []
	for c in cc:
		flag_list.append(chr(ord(c) + offset))
	return "".join(flag_list)
