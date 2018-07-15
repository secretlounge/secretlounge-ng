import re
from string import Formatter

from src.globals import *

class NumericEnum(Enum):
	def __init__(self, names):
		d = {name: i for i, name in enumerate(names)}
		super(NumericEnum, self).__init__(d)

class CustomFormatter(Formatter):
	def convert_field(self, value, conversion):
		if conversion == "x": # escape
			return escape_html(value)
		elif conversion == "t": # date[t]ime
			return format_datetime(value)
		elif conversion == "d": # time[d]elta
			return format_timedelta(value)
		return super(CustomFormatter, self).convert_field(value, conversion)

# definition of reply class and types

class Reply():
	def __init__(self, type, **kwargs):
		self.type = type
		self.kwargs = kwargs

types = NumericEnum([
	"CUSTOM",
	"SUCCESS",
	"BOOLEAN_CONFIG",

	"CHAT_JOIN",
	"CHAT_LEAVE",
	"USER_IN_CHAT",
	"USER_NOT_IN_CHAT",
	"GIVEN_COOLDOWN",
	"PROMOTED_MOD",
	"PROMOTED_ADMIN",
	"KARMA_THANK_YOU",
	"KARMA_NOTIFICATION",

	"ERR_NO_REPLY",
	"ERR_NOT_IN_CACHE",
	"ERR_NO_USER",
	"ERR_ALREADY_WARNED",
	"ERR_COOLDOWN",
	"ERR_BLACKLISTED",
	"ERR_ALREADY_UPVOTED",
	"ERR_UPVOTE_OWN_MESSAGE",
	"ERR_SPAMMY",

	"USER_INFO",
	"USER_INFO_MOD",
	"USERS_INFO",
	"USERS_INFO_EXTENDED",

	"PROGRAM_VERSION",
	"HELP_MODERATOR",
	"HELP_ADMIN",
])

# formatting of these as user-readable text

def em(s):
	# make commands clickable by excluding them from the formatting
	s = re.sub(r'[^a-z0-9_-]/[A-Za-z]+\b', r'</em>\g<0><em>', s)
	return "<em>" + s + "</em>"

def smiley(n):
	if n <= 0: return ":)"
	elif n == 1: return ":|"
	elif n <= 3: return ":/"
	else: return ":("

format_strs = {
	types.CUSTOM: "{text}",
	types.SUCCESS: "☑",
	types.BOOLEAN_CONFIG: lambda enabled, **_:
		"<b>{description!x}</b>: " + (enabled and "enabled" or "disabled"),

	types.CHAT_JOIN: em("You joined the chat!"),
	types.CHAT_LEAVE: em("You left the chat!"),
	types.USER_IN_CHAT: em("You're already in the chat!"),
	types.USER_NOT_IN_CHAT: em("You're not in the chat yet! Use /start to join."),
	types.GIVEN_COOLDOWN: lambda deleted, **_:
		em( "You've been handed a cooldown of {duration!d} for this message"+
			(deleted and " (message also deleted)" or "") ),
	types.PROMOTED_MOD: em("You've been promoted to moderator, run /modhelp for a list of commands."),
	types.PROMOTED_ADMIN: em("You've been promoted to admin, run /adminhelp for a list of commands."),
	types.KARMA_THANK_YOU: em("You just gave this user some sweet karma, awesome!"),
	types.KARMA_NOTIFICATION: 
		em( "You've just been given sweet karma! (check /info to see your karma"+
			" or /toggleKarma to turn these notifications off)" ),

	types.ERR_NO_REPLY: em("You need to reply to a message to use this command."),
	types.ERR_NOT_IN_CACHE: em("Message not found in cache... (24h passed or bot was restarted)"),
	types.ERR_NO_USER: em("No user found by that name!"),
	types.ERR_COOLDOWN: em("Your cooldown expires at {until!t}"),
	types.ERR_ALREADY_WARNED: em("A warning has already been issued for this message."),
	types.ERR_BLACKLISTED: lambda reason, contact, **_:
		em( "You've been blacklisted" + (reason and " for {reason!x}" or "") )+
		( em("\ncontact:") + " {contact}" ) if contact else "",
	types.ERR_ALREADY_UPVOTED: em("You already upvoted this message."),
	types.ERR_UPVOTE_OWN_MESSAGE: em("You can't upvote your own message."),
	types.ERR_SPAMMY: em("Your message has not been sent. Avoid sending messages too fast, try again later."),

	types.USER_INFO: lambda warnings, cooldown, **_:
		"<b>id</b>: {id}, <b>username</b>: {username!x}, <b>rank</b>: {rank_i} ({rank}), "+
		"<b>karma</b>: {karma}\n"+
		"<b>warnings</b>: {warnings} " + smiley(warnings)+
		( " (one warning will be removed on {warnExpiry!t})" if warnings > 0 else "" ) + ", "+
		"<b>cooldown</b>: "+
		( cooldown and "yes, until {cooldown!t}" or "no" ),
	types.USER_INFO_MOD: lambda cooldown, **_:
		"<b>id</b>: {id}, <b>username</b>: anonymous, <b>rank</b>: n/a, "+
		"<b>karma</b>: {karma}\n"+
		"<b>cooldown</b>: "+
		( cooldown and "yes, until {cooldown!t}" or "no" ),
	types.USERS_INFO: "<b>{count}</b> <i>users</i>",
	types.USERS_INFO_EXTENDED:
		"<b>{active}</b> <i>active</i>, {inactive} <i>inactive and</i> "+
		"{blacklisted} <i>blacklisted users</i> (<i>total</i>: {total})",

	types.PROGRAM_VERSION: "secretlounge-ng v{version} ~ https://github.com/sfan5/secretlounge-ng",
	types.HELP_MODERATOR:
		"<i>Moderators can use the following commands</i>:\n"+
		"  /modhelp - show this text\n"+
		"  /modsay &lt;message&gt; - send an official moderator message\n"+
		"\n"+
		"<i>Or reply to a message and use</i>:\n"+
		"  /info - get info about the user that sent this message\n"+
		"  /warn - warn the user that sent this message (cooldown)\n"+
		"  /delete - delete a message and warn the user",
	types.HELP_ADMIN: 
		"<i>Admins can use the following commands</i>:\n"+
		"  /adminhelp - show this text\n"+
		"  /adminsay &lt;message&gt; - send an official admin message\n"+
		"  /motd &lt;message&gt; - set the welcome message\n"+
		"  /mod &lt;username&gt; - promote an user to the moderator rank\n"+
		"  /admin &lt;username&gt; - promote an user to the admin rank\n"+
		"\n"+
		"<i>Or reply to a message and use</i>:\n"+
		"  /blacklist [reason] - blacklist the user who sent this message",
}

def formatForTelegram(m):
	s = format_strs[m.type]
	if type(s).__name__ == "function":
		s = s(**m.kwargs)
	return CustomFormatter().format(s, **m.kwargs)

