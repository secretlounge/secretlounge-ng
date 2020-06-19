import time
import struct
import unicodedata
import dbm.gnu as gdbm
from hashlib import md5

IDENT_TEXT = 1
IDENT_FILEID = 2

class DuplicateDb():
	def __init__(self, path):
		self.db = gdbm.open(path, 'cf')
	def exists(self, ident, _hash):
		k = _hash[:15] + bytes([ident])
		#print(k.hex())
		return self.db.get(k) is not None
	def add(self, ident, _hash):
		k = _hash[:15] + bytes([ident])
		now = int(time.time())
		self.db[k] = struct.pack("!L", now)
	def sync(self):
		self.db.sync()

def hash(o):
	if type(o) == str:
		o = o.encode('ascii')
	assert type(o) == bytes
	return md5(o).digest()

#

def unicat(cat):
	if type(cat) == list:
		return "".join(unicat(cat1) for cat1 in cat)
	return "".join(chr(i) for i in range(0xffff) if unicodedata.category(chr(i)) == cat)

REMOVE = set(" \"'()*+-<=>[]{}" + unicat(["Zs", "Pd", "Pi", "Pf", "Ps", "Pe"]))
REPLACE_DOT = set("!#$%&,./:;?@\\^_`|~" + unicat("Po"))
STRIP = "."

def fold_and_hash(si):
	si = si.casefold()
	so = ""
	l1, l2 = '', ''
	for c in si:
		if c in REMOVE:
			continue
		elif c in REPLACE_DOT:
			c = '.'
			if l1 == c:
				continue
		elif c == '\n':
			if l1 == c or l1 == '':
				continue
		else:
			if c == l1 and l1 == l2:
				continue
		so += c
		l2 = l1
		l1 = c
	so = "\n".join(l.strip(STRIP) for l in so.split("\n"))
	#return so
	return hash(so.encode('utf-8', 'ignore'))
