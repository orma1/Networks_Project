import { BrowserWindow, ipcMain } from 'electron';

import { scanDnsServers } from './util/dns_scanner.js';

import { generateNewZonePayload } from './util/generate_zone.js';

import { getInfrastructureConfig } from './util/config_loader.js';

const mock_data = {
	alert: 'DNSSEC BOGUS',

	ip: '6.6.6.6',

	id: 0,
};

const config = getInfrastructureConfig();

const apiHost = config.API.bind_ip;

const apiPort = config.API.bind_port;

let count = 0;

export function get_data(mainWindow: BrowserWindow) {
	mainWindow.webContents.send('keyword', mock_data);
}

export function get_data_interval(mainWindow: BrowserWindow) {
	const replica_mock_data = { ...mock_data };

	setInterval(async () => {
		replica_mock_data.id = mock_data.id + count;

		count++;

		mainWindow.webContents.send('keyword_interval', replica_mock_data);
	}, 1000);
}

// ApiManager.ts

// Controller for UI updates

export async function pushDnsStatusToUI(mainWindow: BrowserWindow) {
	const statusFlags = await scanDnsServers();

	mainWindow.webContents.send('dns_status', statusFlags);
}

export async function pushToUI(
	eventName: string,

	payload: LegalPayloads,

	mainWindow: BrowserWindow,
) {
	mainWindow.webContents.send(eventName, payload);
}

export async function debugDnsScanner() {
	const statusFlags = await scanDnsServers();

	console.log('[DEBUG] DNS Server Status Flags:', statusFlags);
}

let previousState = '';

export function startWatchdog(mainWindow: BrowserWindow, intervalMs = 3000) {
	// Poll the Python servers in the background

	setInterval(async () => {
		const currentStatus = await scanDnsServers();

		const currentStateString = JSON.stringify(currentStatus);

		// Only send the event if the health status actually changed

		if (currentStateString !== previousState) {
			// Shouting down the "server-status" channel

			mainWindow.webContents.send('status', currentStatus);

			previousState = currentStateString;
		}
	}, intervalMs);
}

export function registerZoneHandlers() {
	ipcMain.handle('api:fetch-zone-list', async (_, nameServer: string) => {
		try {
			const apiUrl = `http://${apiHost}:${apiPort}/api/zones/list/${nameServer}`;

			const response = await fetch(apiUrl);

			if (!response.ok) return [];

			return await response.json();
		} catch (error) {
			if (error instanceof Error) console.log(error);

			return [];
		}
	});

	ipcMain.handle(
		'api:fetch-zone',

		async (_, nameServer: string, zoneName: string) => {
			console.log('[Backend] Fatching Zones');

			if (apiHost === undefined || apiPort === undefined) {
				console.log('[Backend] Problem with the URL');

				return null;
			}

			if (nameServer === undefined || zoneName === undefined) {
				console.log('[Frontend] Problem with the UI logic');
			}

			try {
				const apiUrl = `http://${apiHost}:${apiPort}/api/zone/${nameServer}/${zoneName}`;

				const response = await fetch(apiUrl);

				if (!response.ok) return null;

				return await response.json();
			} catch (error) {
				console.error('[Backend] Network error fetching zone:', error);

				return null;
			}
		},
	);

	ipcMain.handle(
		'api:save-zone',

		async (_, nameServer: string, zoneName: string, zoneData: ZoneData) => {
			try {
				const apiUrl = `http://${apiHost}:${apiPort}/api/zone/${nameServer}/${zoneName}`;

				const response = await fetch(apiUrl, {
					method: 'POST',

					headers: { 'Content-Type': 'application/json' },

					body: JSON.stringify(zoneData),
				});

				if (!response.ok) {
					const errorData = await response.json().catch(() => ({}));

					throw new Error(
						errorData.detail ||
							`FastAPI Error: ${response.statusText}`,
					);
				}

				return {
					success: true,
					message: (await response.json()).message,
				};
			} catch (error) {
				if (error instanceof Error)
					return { success: false, error: error.message };
			}
		},
	);

	ipcMain.handle(
		'create-new-zone',

		async (_, nameServer: string, zoneName: string) => {
			if (!zoneName || typeof zoneName !== 'string') {
				return { success: false, error: 'Invalid zone name provided.' };
			}

			// Clean the input to just the base name (e.g., "project.homelab") for the URL

			const cleanName = zoneName.trim().replace(/\.$/, '');

			// Generate the strict JSON payload

			const payload = generateNewZonePayload(cleanName);

			try {
				// Send it directly to your FastAPI backend using the dynamic host/port and nameServer

				const response = await fetch(
					`http://${apiHost}:${apiPort}/api/zone/${nameServer}/${cleanName}`,

					{
						method: 'POST',

						headers: {
							'Content-Type': 'application/json',
						},

						body: JSON.stringify(payload),
					},
				);

				if (response.ok) {
					console.log(
						`[Electron] Successfully created new zone file: ${cleanName}.zone in ${nameServer}`,
					);

					return { success: true };
				} else {
					const errorText = await response.text();

					console.error(
						`[Electron] FastAPI rejected zone creation:`,

						errorText,
					);

					return {
						success: false,
						error: `API Error: ${response.statusText}`,
					};
				}
			} catch (error) {
				console.error('[Electron] Network error creating zone:', error);

				if (error instanceof Error) {
					return { success: false, error: error.message };
				}

				return { success: false, error: 'Unknown network error.' };
			}
		},
	);

	ipcMain.handle(
		'api:delete-zone',

		async (_, nameServer: string, zoneName: string) => {
			try {
				const response = await fetch(
					`http://${apiHost}:${apiPort}/api/zone/${nameServer}/${zoneName}`,

					{
						method: 'DELETE',
					},
				);

				if (response.ok) return { success: true };

				const errorText = await response.text();

				return { success: false, error: errorText };
			} catch (error) {
				if (error instanceof Error)
					return { success: false, error: error.message };
			}
		},
	);

	// ===============================

	//        Config Handlers

	// ===============================

	ipcMain.handle('api:fetch-config', async (_, configName: string) => {
		console.log('[Backend] Fatching Config');

		if (apiHost === undefined || apiPort === undefined) {
			console.log('[Backend] Problem with the URL');

			return null;
		}

		if (configName === undefined) {
			console.log('[Frontend] Problem with the UI logic');
		}

		try {
			const apiUrl = `http://${apiHost}:${apiPort}/api/config/${configName}`; // (root, auth, resolver, no need adding _config)

			const response = await fetch(apiUrl);

			if (!response.ok) return null;

			return await response.json();
		} catch (error) {
			console.error('[Backend] Network error fetching config:', error);

			return null;
		}
	});

	ipcMain.handle(
		'api:save-config',

		async (_, configName: string, configData: ConfigFormat) => {
			try {
				const apiUrl = `http://${apiHost}:${apiPort}/api/config/${configName}`;

				const response = await fetch(apiUrl, {
					method: 'POST',

					headers: { 'Content-Type': 'application/json' },

					body: JSON.stringify(configData),
				});

				if (!response.ok) {
					const errorData = await response.json().catch(() => ({}));

					throw new Error(
						errorData.detail ||
							`FastAPI Error: ${response.statusText}`,
					);
				}

				return {
					success: true,
					message: (await response.json()).message,
				};
			} catch (error) {
				if (error instanceof Error)
					return { success: false, error: error.message };
			}
		},
	);
}
