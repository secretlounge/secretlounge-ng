// Replace or append the following definitions to .../share/netdata/web/dashboard_info.js
netdataDashboard.context = {
	'secretlounge.active_users': {
		decimalDigits: 0,
		valueRange: "[0, null]"
	},
	'secretlounge.users': {
		height: 1.5,
		decimalDigits: 0,
		valueRange: "[0, null]",
		colors: NETDATA.colors[1] + " " + NETDATA.colors[13]
	},

	'secretlounge.message_types': {
		height: 1.2
	},

	'secretlounge.queue_size': {
		decimalDigits: 0
	},
	'secretlounge.api_calls': {
		decimalDigits: 0,
		colors: NETDATA.colors[2] + " " + NETDATA.colors[7]
	},

	'secretlounge.cache_size': {
		decimalDigits: 0
	},
	'secretlounge.warn_given': {
		height: 0.5
	},
	'secretlounge.karma_given': {
		height: 0.5
	},
};
