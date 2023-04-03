#!/usr/bin/env python3
import sys
import logging

from blacklist import detect_dbs, print_function_help

# backend

def list_privileged_users(db, cond="rank > 0"):
	sql = "SELECT id, username, realname, rank, left, lastActive FROM users WHERE " + cond
	c = db.execute(sql)
	ret = {}
	for row in c:
		user = ("@" + row[1]) if row[1] is not None else row[2]
		active = None if row[4] is not None else row[5]
		ret[row[0]] = (user, row[3], active)
	return ret

def set_user_rank(db, id, rank):
	c = db.execute("SELECT 1 FROM users WHERE id = ?", (id, ))
	if c.fetchone() is None:
		return False
	db.modify("UPDATE users SET rank = ? WHERE id = ?", (rank, id))
	return True

# frontend

def c_list(d, argv):
	"""list [db name] [-a|-m]
		List users with special permissions
		-a to only show admins, -m only mods"""
	if len(d) == 1: # implicit db
		argv = [ next(x for x in d.keys()) ] + argv
	if len(argv) not in (1, 2):
		return Exception
	if argv[0] == '*':
		for name in d.keys():
			argv[0] = name
			print("== %s" % name)
			c_list(d, argv)
		return

	db = d[argv[0]]
	if len(argv) > 1:
		cond = ({"-a": "rank = 100", "-m": "rank = 10"}).get(argv[1])
		if not cond:
			return Exception
		t = list_privileged_users(db, cond)
	else:
		t = list_privileged_users(db)
	if len(t) == 0:
		return print("No results")

	fmt = "{:<12s} {:<28s} {:>4s} {:^18s}"
	print(fmt.format("ID", "username", "rank", "last active"))
	for id, e in t.items():
		active = str(e[2] or "(left chat)")[:16]
		print(fmt.format(str(id), e[0], str(e[1]), active))

def c_set(d, argv):
	"""set [db name] <user id> [rank]
		Set user permission level (admin, mod, user)
		rank defaults to 0 if not given"""
	if len(d) == 1: # implicit db
		argv = [ next(x for x in d.keys()) ] + argv
	if len(argv) not in (2, 3):
		return Exception
	if argv[0] == '*': # db wildcard
		for name in d.keys():
			argv[0] = name
			print("== %s" % name)
			c_set(d, argv)
		return

	db = d[argv[0]]
	id = int(argv[1])
	rank = "user" if len(argv) < 3 else argv[2]

	if not rank.isdigit():
		rank = ({"admin": 100, "mod": 10, "user": 0}).get(rank.lower(), -1)
	rank = int(rank)
	if rank not in (100, 10, 0):
		return logging.error("Invalid rank given")
	if set_user_rank(db, id, rank):
		logging.info("Success")
	else:
		logging.error("No such user")

def usage(actions):
	print("Utility for managing user permissions (sqlite only)")
	print("Usage: perms.py <action> [arguments...]")
	print("Note that the db name MUST NOT be specified if there's only one db, "
		"but MUST be specified if there are multiple.")
	print("Actions:")
	print_function_help(actions)

def main(argv):
	logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M", level=logging.INFO)

	actions = {
		"list": c_list, "set": c_set
	}

	if len(argv) > 0:
		d = detect_dbs()

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
