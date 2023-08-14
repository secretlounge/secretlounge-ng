secretlounge-ng
---------------

Rewrite of [secretlounge](https://web.archive.org/web/20200920053736/https://github.com/6697/secretlounge), a bot to make an anonymous group chat on Telegram.

The bot accepts messages, pictures, videos, etc. from any user and relays it to all other active users without revealing the author.

## Setup

You will need a Linux server or computer with Python 3 installed and access to the command line.

```bash
pip3 install -e .
cp config.yaml.example config.yaml
# Edit config.yaml with your favorite text editor
./secretlounge-ng
```

To run the bot in the background use a systemd service (preferred) or screen/tmux.

Note that you can also install it as a normal Python module and run it from anywhere
like `python3 -m secretlounge_ng`, which I won't explain here.

## @BotFather Setup

Message [@BotFather](https://t.me/BotFather) and configure your bot as follows:

* `/setprivacy`: enabled
* `/setjoingroups`: disabled
* `/setcommands`: paste the command list below

### Command list

```
start - Join the chat (start receiving messages)
stop - Leave the chat (stop receiving messages)
users - Find out how many users are in the chat
info - Get info about your account
dm - Send your username to a message's author
sign - Sign a message with your username
s - Alias of sign
tsign - Sign a message with your tripcode
t - Alias of tsign
motd - Show the welcome message
version - Get version & source code of this bot
modhelp - Show commands available to moderators
adminhelp - Show commands available to admins
toggledebug - Toggle debug mode (sends back all messages to you)
togglekarma - Toggle karma notifications
togglerequests - Toggle DM request notifications
tripcode - Show or set the tripcode for your messages
```

## FAQ

1. **How do I unban a blacklisted user from my bot?**

To unban someone you need their Telegram User ID (preferred) or username/profile name.
If you have a name you can use `./util/blacklist.py find` to search your bot's database for the user record.

You can then run `./util/blacklist.py unban 12345678` to remove the ban.

2. **How do I demote somone I promoted to mod/admin at some point?**

If you already have an User ID in mind, proceed below.
Otherwise you can either use the find utility like explained above or run
`./util/perms.py list` to list all users with elevated rank.

Simply run `./util/perms.py set 12345678 user` to remove the users' privileges.

This can also be used to grant an user higher privileges by exchanging the last argument with "*mod*" or "*admin*".

3. **What is the suggested setup to run multiple bots?**

The `blacklist.py` and `perms.py` script, including advanced functions like blacklist syncing
(`./util/blacklist.py sync`), support a structure like the following where each bot
has its own subdirectory:

```
root folder
\-- bot1
  \-- db.sqlite
  \-- config.yaml
\-- bot2
  \-- db.sqlite
  \-- ...
\-- ...
\-- README.md
\-- secretlounge-ng
```

4. **Is this bot really anonymous?**

When using the source in this repository*¹*, unless you reveal yourself,
ordinary users in the bot have zero possibilities of discovering your Telegram user.

Mods and admins in the bot can tell the authors of recent messages apart through a pseudo-random
ID returned by the `/info` command. This ID changes every 24 hours, posts also expire from
the cache after 24 hours*²* (or if secretlounge-ng is restarted) meaning that they
become unable to be deleted or their authors determined.

People with access to the server the bot runs on have no direct, but a variety of
indirect ways to determine who wrote a particular message.

*¹*: It is impossible to ascertain this from afar. You have to trust the bot owner either way.

*²*: Sophisticated attacks are possible to track continously active users over a longer timeframe. It is not expected that a human can perform this.

All of these assessments presume a sufficient user population in the bot so that anyone could blend in.

5. **Why don't polls work?**

Telegram bots are able to create new polls and forward messages (including authorship), but they can't forward the poll itself as with other message types.
Working around this is possible with some disadvantages, but has not been implemented yet.

6. **Is this code maintained?**

This codebase is in active use [over here](https://t.me/s/secretloungeproject).
Updates are made either if there's something broken or when the author feels like it.

## Notable forks

* [CatLounge](https://github.com/CatLounge/catlounge-ng-meow) - has numerous new features including specifying cooldown time
* [Furry fork](https://github.com/dogmike/secretlounge-ng) - not sure, but there's a bunch of things
