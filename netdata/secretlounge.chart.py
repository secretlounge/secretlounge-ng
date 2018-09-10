# -*- coding: utf-8 -*-
import os.path
import socket
import json

from bases.FrameworkServices.SimpleService import SimpleService

# default module values
update_every = 4
priority = 40000
retries = 60

CHARTS = {
	'users': {
		#           ???  description    unit      group     internal id        type
		'options': [None, 'All Users', 'users', 'users', 'secretlounge.users', 'area'],
		'lines': [
			# internal id   description    type     scale   divide
			['users_total',  'total',  'absolute',   1,     1],
			['users_joined', 'active', 'absolute', 1, 1],
		]
	},
	'active_users': {
		'options': [None, 'Active Users', 'users', 'users', 'secretlounge.active_users', 'line'],
		'lines': [
			['active_users_15m', '15m', 'absolute', 1, 1],
			['active_users_1h', '1h', 'absolute', 1, 1],
			['active_users_12h', '12h', 'absolute', 1, 1],
		]
	},

	'message_types': {
		'options': [None, 'Messages Sent by Type', 'messages', 'messages', 'secretlounge.message_types', 'stacked'],
		'lines': [
			['message_type_text', 'text', 'incremental', 1, 1],
			['message_type_sticker', 'sticker', 'incremental', 1, 1],
			['message_type_gif', 'gif', 'incremental', 1, 1],
			['message_type_media', 'media', 'incremental', 1, 1],
		]
	},

	'queue_size': {
		'options': [None, 'Queue Size', 'messages', 'queue', 'secretlounge.queue_size', 'line'],
		'lines': [
			['queue_size', 'count', 'absolute', 1, 1],
		]
	},
	'queue_latency': {
		'options': [None, 'Queue Latency', 'seconds', 'queue', 'secretlounge.queue_latency', 'line'],
		'lines': [
			['queue_latency_avg', 'avg', 'absolute', 1, 1],
			['queue_latency_95', '95th', 'absolute', 1, 1],
		]
	},
	'api_calls': {
		'options': [None, 'API Calls', 'calls', 'queue', 'secretlounge.api_calls', 'line'],
		'lines': [
			['api_calls', 'count', 'absolute', 1, 1],
		]
	},

	'cache_size': {
		'options': [None, 'Cache Size', 'messages', 'other', 'secretlounge.cache_size', 'line'],
		'lines': [
			['cache_size', 'count', 'absolute', 1, 1],
		]
	},
	'warn_given': {
		'options': [None, 'Warnings given', 'warnings', 'other', 'secretlounge.warn_given', 'line'],
		'lines': [
			['warnings_given', 'count', 'incremental', 1, 1],
		]
	},
	'karma_given': {
		'options': [None, 'Karma given', 'karma', 'other', 'secretlounge.karma_given', 'line'],
		'lines': [
			['karma_given', 'count', 'incremental', 1, 1],
		]
	},
}
ORDER = list(CHARTS.keys())

def try_connect(sockpath):
	s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	try:
		s.connect(sockpath)
	except:
		return None
	return s

class Service(SimpleService):
	def __init__(self, configuration=None, name=None):
		SimpleService.__init__(self, configuration=configuration, name=name)
		self.socket = None
		self.sockpath = self.configuration['socket']
		self.order = ORDER
		self.definitions = CHARTS

		# props by chart type, 'absolute' / 'incremental'
		self.abs_props = (
			"users_total", "users_joined", "active_users_15m", "active_users_1h", "active_users_12h",
			#
			"api_calls", "queue_size", "queue_latency_avg", "queue_latency_95",
			"cache_size",
		)
		self.incr_props = (
			#
			"message_type_text", "message_type_sticker", "message_type_gif", "message_type_media",
			#
			"warnings_given", "karma_given",
		)

		# init data
		self.data = dict()
		for p in set(self.abs_props) | set(self.incr_props):
			self.data[p] = 0

	def _read_data(self):
		if self.socket is None:
			self.socket = try_connect(self.sockpath)
			if self.socket is None:
				return None
		try:
			self.socket.send(b".")
		except socket.error as e:
			self.socket.close()
			self.socket = None
			return self._read_data() # retry
		return json.loads(self.socket.recv(1024).decode('utf-8'))

	def check(self):
		if self.socket is None:
			self.socket = try_connect(self.sockpath)
		return True # assume it's working to avoid plugin reloads

	def get_data(self):
		j = self._read_data()
		if j is None:
			self.warning("unable to get any stats data")
			return None

		for p in self.abs_props:
			self.data[p] = j.get(p, None)
		for p in self.incr_props:
			self.data[p] += j.get(p, 0)

		return self.data
