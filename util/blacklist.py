#!/usr/bin/env python3
import sys
import os
import logging
import sqlite3
import readline # for input()
from datetime import datetime, timedelta
from time import sleep

# database
# NOTE: a few other utilities import this code

class Database():
	def __init__(self, path):
		t = sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES
		self.db = sqlite3.connect(path, detect_types=t)
		self.db.row_factory = sqlite3.Row
	def modify_custom(self, func):
		while True:
			try:
				func()
			except sqlite3.OperationalError as e:
				if "database is locked" in str(e):
					continue # just retry, sqlite will do the waiting for us
				raise
			break
		self.db.commit()
	def modify(self, sql, args=()):
		self.modify_custom(lambda: self.db.execute(sql, args))
	# eh...
	def execute(self, *args, **kwargs):
		return self.db.execute(*args, **kwargs)
	def commit(self, *args, **kwargs):
		return self.db.commit(*args, **kwargs)

def detect_dbs():
	if os.path.exists("./db.sqlite"): # no fancy structure...
		return {"default": Database("./db.sqlite")}
	d = {}
	# expects the following directory structure:
	#   root dir
	#   \ bot1
	#     \ db.sqlite
	#     \ ...
	#   \ bot2
	#     \ db.sqlite
	#     \ ...
	#   \ README.md
	#   \ secretlounge-ng
	#   \ ...
	for e in os.scandir("."):
		if e.is_dir():
			path = os.path.join(e.path, "db.sqlite")
			if os.path.exists(path):
				d[e.name] = Database(path)
	return d

# backend

def ban_user(db, id, reason):
	c = db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (id, ))
	if c.fetchone()[0] == 0:
		# user was never here, add an placeholder entry to still ban them
		nodate = datetime.utcfromtimestamp(0)
		u = {
			"id": id,
			"realname": "",
			"rank": -10,
			"joined": nodate,
			"left": nodate,
			"lastActive": nodate,
			"blacklistReason": reason,
			"warnings": 0,
			"karma": 0,
			"hideKarma": 0,
			"debugEnabled": 0,
		}
		sql = "INSERT INTO users (" + ( ", ".join(u.keys()) ) + ") VALUES (" + ( ", ".join("?" for _ in u) ) + ")"
		db.modify(sql, tuple(u.values()))
		return 0, 1
	# is this user already banned?
	c = db.execute("SELECT COUNT(*) FROM users WHERE id = ? AND rank > ?", (id, -10))
	if c.fetchone()[0] == 0:
		return 0, 0
	# update user values to ban them
	param = (-10, datetime.now(), reason, id)
	db.modify("UPDATE users SET rank = ?, left = ?, blacklistReason = ? WHERE id = ?", param)
	return 1, 0

def unban_user(db, id):
	c = db.execute("SELECT realname, left FROM users WHERE id = ? AND rank = ?", (id, -10))
	row = c.fetchone()
	if row is None:
		return 0
	if row[0] == "" and row[1] == datetime.utcfromtimestamp(0):
		# this is a placeholder entry, just delete it instead
		db.modify("DELETE FROM users WHERE id = ?", (id, ))
	else:
		db.modify("UPDATE users SET rank = ?, blacklistReason = NULL WHERE id = ?", (0, id))
	return 1

def sync(d):
	interval = 60
	last_update = datetime.utcfromtimestamp(0.0)
	logging.info("Running periodic blacklist sync (every %ds)", interval)
	while True:
		now = datetime.now() - timedelta(seconds=5)
		# find all blacklists that happened since our last update
		l = []
		for name, db in d.items():
			c = db.execute("SELECT id, blacklistReason FROM users WHERE rank = ? AND left >= ?", (-10, last_update))
			for row in c:
				reason = row[1] or ""
				if reason.endswith("]"): # transferred from elsewhere?
					continue
				l.append((row[0], reason, name))
		# apply the same bans on other instances
		for id, reason, from_name in l:
			reason = reason + " [" + from_name + "]"
			stat1, stat2 = 0, 0
			for db in d.values():
				a, b = ban_user(db, id, reason)
				stat1 += a; stat2 += b
			if stat1 + stat2 > 0:
				logging.info("Transferred ban of user id %d orignated from %s (%d-%d)", id, from_name, stat1, stat2)
		# Zzz..
		last_update = now
		sleep(interval)

def find_user(db, term):
	attrs = ("username", "realname", "rank", "joined", "left", "lastActive",
		 "cooldownUntil", "blacklistReason", "warnings", "warnExpiry", "karma")
	sql = "SELECT id, " + ",".join(attrs) + " FROM users WHERE"
	sql += " username LIKE ? OR realname LIKE ?"
	args = ["%" + term + "%", "%" + term + "%"]
	# numeric argument also searches for ID match
	if term.isdigit():
		sql += " OR id = ?"
		args += [int(term)]
	c = db.execute(sql, args)
	ret = {}
	for row in c:
		ret[row[0]] = row[1:]
	return ret, attrs

# frontend

def c_ban(d, argv):
	"""ban <user id> [reason]\nManually blacklist specified user"""
	if len(argv) < 2:
		return Exception
	id = int(argv[0])
	reason = " ".join(argv[1:])
	stat1, stat2 = 0, 0
	for db in d.values():
		a, b = ban_user(db, id, reason)
		stat1 += a; stat2 += b
	logging.info("Success (%d-%d)", stat1, stat2)

def c_unban(d, argv):
	"""unban <user id>\nUnban specified user"""
	if len(argv) != 1:
		return Exception
	id = int(argv[0])
	stat = 0
	for db in d.values():
		stat += unban_user(db, id)
	if stat == 0:
		return logging.warning("This user wasn't blacklisted anywhere.")
	logging.info("Success (%d)", stat)

def c_find(d, argv):
	"""find\nInteractive prompt that searches users"""
	if len(argv) != 0:
		return Exception
	def str_helper(x):
		if x is None:
			return "NULL"
		if isinstance(x, datetime):
			return str(x)[:19]
		return str(x)
	if sys.platform == 'linux':
		prompt_str = "\033[35mfind>\033[0m "
	else:
		prompt_str = "find> "
	while True:
		try:
			p = input(prompt_str).strip()
		except (KeyboardInterrupt, EOFError):
			p = ""
		if not p:
			break

		any_ = False
		for dbname in sorted(d.keys()):
			ret, attrs = find_user(d[dbname], p)
			if len(ret) == 0:
				continue
			any_ = True
			print("In %s:" % dbname)
			print( ("%-10s" % "ID") + "|".join(attrs) )
			for id, data in ret.items():
				tmp = (str_helper(x) for x in data)
				print( ("%-10s" % id) + "|".join(tmp) )

		if any_:
			print("")

def c_sync(d, argv):
	"""sync\nSynchronize blacklisted users (runs in foreground)"""
	if len(argv) != 0:
		return Exception
	if len(d) < 2:
		return logging.error("You have only one database, syncing makes no sense!")
	sync(d)

def usage(actions):
	print("Utility for managing blacklists (sqlite only)")
	print("Usage: blacklist.py <action> [arguments...]")
	fmt = "    %-" + str( max(len(f.__doc__.split("\n")[0]) for f in actions.values()) + 4 ) + "s%s"
	print("Actions:")
	for f in actions.values():
		s = list(x.strip() for x in f.__doc__.split("\n"))
		for i, text in enumerate(s[1:]):
			print(fmt % (s[0] if i == 0 else "", text))

def main(argv):
	logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M", level=logging.INFO)

	actions = {
		"ban": c_ban, "unban": c_unban, "find": c_find, "sync": c_sync
	}

	if len(argv) > 0:
		d = detect_dbs()
		if len(d) == 0:
			logging.error("No databases detected, exiting!")
			exit(1)
		logging.info("Detected %d database(s): %s", len(d), ", ".join(d.keys()))

		action = argv[0].lower()
		if action not in actions.keys():
			logging.error("Unknown action")
		else:
			ret = actions[action](d, argv[1:])
			if ret is not Exception: # lol
				exit(0)

	usage(actions)
	exit(1)
		
if __name__ == "__main__":
	main(sys.argv[1:])
