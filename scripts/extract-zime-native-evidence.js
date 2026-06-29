#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const DEFAULT_LIB_DIR = '/opt/yidongyun/client/opt/chuanyun-vdi-client/resources/app.asar.unpacked/node_modules/chuanyunAddOn-zte/ccsdk/lib';

const KEYWORDS = [
  'ZIMEDataEngine',
  'ZIMEDtlsSession',
  'ZIMEQuic',
  'ZIMESctp',
  'DataChannel',
  'lsquic',
  'sctp',
  'ACK',
  'PING',
  'packet',
  'stream',
  'retrans',
  'heartbeat',
  'keepalive',
  'timeout',
  'send',
  'recv',
  'loss',
  'window',
  'handshake',
];

function usage() {
  console.error('Usage: node scripts/extract-zime-native-evidence.js [lib-dir]');
  process.exit(2);
}

function run(command, args) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) {
    return { ok: false, error: result.error.message, stdout: '', stderr: '' };
  }
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: result.stdout || '',
    stderr: result.stderr || '',
  };
}

function uniqueSorted(lines) {
  return [...new Set(lines.map((line) => line.trim()).filter(Boolean))].sort();
}

function pickKeywordLines(text, limit = 240) {
  const re = new RegExp(KEYWORDS.map((item) => item.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'i');
  return uniqueSorted(text.split(/\r?\n/).filter((line) => re.test(line))).slice(0, limit);
}

function parseDynamicNeeded(readelfDynamic) {
  return readelfDynamic
    .split(/\r?\n/)
    .map((line) => line.match(/\(NEEDED\)\s+.*\[(.+)]/))
    .filter(Boolean)
    .map((match) => match[1]);
}

function fileReport(file) {
  const readelfSymbols = run('readelf', ['-Ws', file]);
  const readelfDynamic = run('readelf', ['-d', file]);
  const stringsOut = run('strings', ['-a', file]);
  return {
    file,
    exists: fs.existsSync(file),
    size: fs.existsSync(file) ? fs.statSync(file).size : 0,
    dynamicNeeded: readelfDynamic.ok ? parseDynamicNeeded(readelfDynamic.stdout) : [],
    symbolEvidence: readelfSymbols.ok ? pickKeywordLines(readelfSymbols.stdout) : [],
    stringEvidence: stringsOut.ok ? pickKeywordLines(stringsOut.stdout) : [],
    toolErrors: [
      readelfSymbols.ok ? null : `readelf -Ws failed: ${readelfSymbols.error || readelfSymbols.stderr || readelfSymbols.status}`,
      readelfDynamic.ok ? null : `readelf -d failed: ${readelfDynamic.error || readelfDynamic.stderr || readelfDynamic.status}`,
      stringsOut.ok ? null : `strings failed: ${stringsOut.error || stringsOut.stderr || stringsOut.status}`,
    ].filter(Boolean),
  };
}

function main(argv = process.argv.slice(2)) {
  if (argv.includes('-h') || argv.includes('--help')) usage();
  const libDir = argv[0] || DEFAULT_LIB_DIR;
  const targets = ['libcag.so', 'libZIMEDataEngine.so'].map((name) => path.join(libDir, name));
  const configCandidates = [
    path.join(libDir, '..', 'config', 'sdk_config.json'),
    '/opt/yidongyun/client/opt/chuanyun-vdi-client/resources/app.asar.unpacked/node_modules/chuanyunAddOn/ccsdk/uos/config/sdk_config.json',
  ];
  const configs = configCandidates
    .filter((file, index, list) => list.indexOf(file) === index)
    .filter((file) => fs.existsSync(file))
    .map((file) => {
      try {
        const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
        return {
          file,
          version: parsed.ver,
          udpSessionTimeout: parsed.local_setting?.session?.udp_session_timeout,
          ackLoopPeriod: parsed.local_setting?.xe_init?.ack_loop_period,
          heartbeatPeriod: parsed.local_setting?.keepalive?.heartbeat_period,
          keepaliveTimeout: parsed.local_setting?.keepalive?.keepalive_timeout,
          streamOptions: parsed.local_setting?.stream_options,
        };
      } catch (err) {
        return { file, error: err.message };
      }
    });

  console.log(JSON.stringify({
    libDir,
    generatedAt: new Date().toISOString(),
    keywords: KEYWORDS,
    configs,
    libraries: targets.map(fileReport),
    interpretation: {
      libcag: 'Expected to cover access-gateway bootstrap: local_key, server_key, connect_info, connect_reply.',
      libZIMEDataEngine: 'Expected to cover reliable UDP/QUIC/SCTP-style data channels, ACK/PING, packet scheduling, DTLS/session handling.',
      keepaliveBoundary: 'This is static evidence only. Live protocol keepalive still requires carrying SPICE main/display bytes through this transport without SDK startup.',
    },
  }, null, 2));
}

main();
