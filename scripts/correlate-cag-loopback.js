#!/usr/bin/env node
'use strict';

const fs = require('fs');
const {
  decodeLocalSpiceClientDataMessages,
  decodeLocalSpiceClientHandshake,
  decodeLocalSpiceServerDataMessages,
  decodeLocalSpiceServerHandshake,
  parseZteCagDatagram,
} = require('../lib/protocol');

const CLIENT_DISPLAY_LANDMARK_TYPES = new Map([
  [0x0065, 'DISPLAY_INIT'],
]);

const SERVER_DISPLAY_LANDMARK_TYPES = new Map([
  [0x0003, 'SET_ACK'],
  [0x0066, 'MARK'],
  [0x0130, 'DRAW_COPY'],
  [0x013a, 'SURFACE_CREATE'],
]);

function usage() {
  console.error('Usage: node scripts/correlate-cag-loopback.js <cag.pcap> <loopback.pcap> [--window-ms 80] [--limit 12]');
  process.exit(2);
}

function parseArgs(argv) {
  const out = { _: [], windowMs: 80, limit: 12 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--window-ms') out.windowMs = Number(argv[++i] || 0);
    else if (arg === '--limit') out.limit = Number(argv[++i] || 0);
    else out._.push(arg);
  }
  return out;
}

function packetTime(packet) {
  return Number(`${packet.seconds}.${String(packet.micros).padStart(6, '0')}`);
}

function formatTimeNumber(value) {
  if (!Number.isFinite(value)) return null;
  return value.toFixed(6);
}

function parseClassicPcapPackets(file) {
  const buffer = fs.readFileSync(file);
  if (buffer.length < 24) throw new Error('pcap file is too short');
  const magic = buffer.readUInt32LE(0);
  if (magic !== 0xa1b2c3d4 && magic !== 0xd4c3b2a1) {
    throw new Error('only classic little-endian pcap files are supported');
  }
  const linkType = buffer.readUInt32LE(20);
  let offset = 24;
  const packets = [];

  while (offset + 16 <= buffer.length) {
    const seconds = buffer.readUInt32LE(offset);
    const micros = buffer.readUInt32LE(offset + 4);
    const capturedLength = buffer.readUInt32LE(offset + 8);
    const packetOffset = offset + 16;
    offset = packetOffset + capturedLength;

    let ipOffset;
    if (linkType === 1) {
      if (capturedLength < 34 || buffer.readUInt16BE(packetOffset + 12) !== 0x0800) continue;
      ipOffset = packetOffset + 14;
    } else if (linkType === 276) {
      if (capturedLength < 40 || buffer.readUInt16BE(packetOffset) !== 0x0800) continue;
      ipOffset = packetOffset + 20;
    } else {
      throw new Error(`unsupported pcap link type: ${linkType}`);
    }

    const ipHeaderLength = (buffer[ipOffset] & 0x0f) * 4;
    const protocol = buffer[ipOffset + 9];
    const sourceIp = [...buffer.subarray(ipOffset + 12, ipOffset + 16)].join('.');
    const destinationIp = [...buffer.subarray(ipOffset + 16, ipOffset + 20)].join('.');
    const l4Offset = ipOffset + ipHeaderLength;
    if (protocol === 17) {
      const udpLength = buffer.readUInt16BE(l4Offset + 4);
      packets.push({
        seconds,
        micros,
        protocol: 'udp',
        sourceIp,
        destinationIp,
        sourcePort: buffer.readUInt16BE(l4Offset),
        destinationPort: buffer.readUInt16BE(l4Offset + 2),
        payload: buffer.subarray(l4Offset + 8, l4Offset + udpLength),
      });
    } else if (protocol === 6) {
      const tcpHeaderLength = (buffer[l4Offset + 12] >> 4) * 4;
      const ipTotalLength = buffer.readUInt16BE(ipOffset + 2);
      const payloadLength = ipTotalLength - ipHeaderLength - tcpHeaderLength;
      if (payloadLength <= 0) continue;
      packets.push({
        seconds,
        micros,
        protocol: 'tcp',
        sourceIp,
        destinationIp,
        sourcePort: buffer.readUInt16BE(l4Offset),
        destinationPort: buffer.readUInt16BE(l4Offset + 2),
        sequence: buffer.readUInt32BE(l4Offset + 4),
        payload: buffer.subarray(l4Offset + tcpHeaderLength, l4Offset + tcpHeaderLength + payloadLength),
      });
    }
  }

  return packets;
}

function firstRemoteHost(packets) {
  const counts = new Map();
  for (const packet of packets) {
    for (const ip of [packet.sourceIp, packet.destinationIp]) {
      if (ip.startsWith('127.')) continue;
      if (/^(10|172\.16|172\.17|172\.18|172\.19|172\.2\d|172\.3[01]|192\.168)\./.test(ip)) continue;
      counts.set(ip, (counts.get(ip) || 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || '';
}

function direction(packet, remoteHost) {
  if (remoteHost && packet.destinationIp === remoteHost) return 'client->cag';
  if (remoteHost && packet.sourceIp === remoteHost) return 'cag->client';
  return `${packet.sourceIp}:${packet.sourcePort}->${packet.destinationIp}:${packet.destinationPort}`;
}

function hex32(value) {
  return `0x${(Number(value) >>> 0).toString(16).padStart(8, '0')}`;
}

function cagTunnelEvents(file) {
  const packets = parseClassicPcapPackets(file).filter((packet) => packet.protocol === 'udp');
  const remoteHost = firstRemoteHost(packets);
  const events = [];
  for (const packet of packets) {
    let parsed;
    try {
      parsed = parseZteCagDatagram(packet.payload);
    } catch (_) {
      continue;
    }
    if (!parsed.tunnel) continue;
    const tunnel = parsed.tunnel;
    const header = tunnel.header;
    events.push({
      time: packetTime(packet),
      direction: direction(packet, remoteHost),
      source: `${packet.sourceIp}:${packet.sourcePort}`,
      destination: `${packet.destinationIp}:${packet.destinationPort}`,
      typeName: header.packetTypeName,
      sequence16: header.sequence16,
      word0Hex: hex32(header.word0),
      word1Hex: hex32(header.word1),
      word2Hex: hex32(header.word2),
      word3Hex: hex32(header.word3),
      word4Hex: hex32(header.word4),
      word5Hex: hex32(header.word5),
      payloadLength: tunnel.payloadLength,
      tls: tunnel.hasTlsRecord ? {
        offset: tunnel.tlsRecordOffset,
        contentType: tunnel.tlsRecord[0],
        versionHex: tunnel.tlsRecord.subarray(1, 3).toString('hex'),
        length: tunnel.tlsRecord.length >= 5 ? tunnel.tlsRecord.readUInt16BE(3) : null,
      } : null,
    });
  }
  return { remoteHost, events };
}

function reconstructTcpStreams(packets) {
  const groups = new Map();
  for (const packet of packets.filter((item) => item.protocol === 'tcp')) {
    const key = `${packet.sourcePort}->${packet.destinationPort}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(packet);
  }

  const streams = [];
  for (const [key, group] of groups) {
    group.sort((a, b) => (a.sequence - b.sequence) || (packetTime(a) - packetTime(b)));
    const chunks = [];
    const ranges = [];
    let cursor = null;
    let assembledOffset = 0;
    for (const packet of group) {
      if (cursor === null) cursor = packet.sequence;
      const packetEnd = packet.sequence + packet.payload.length;
      if (packetEnd <= cursor) continue;
      if (packet.sequence > cursor) {
        const gapLength = packet.sequence - cursor;
        chunks.push(Buffer.alloc(gapLength));
        assembledOffset += gapLength;
        cursor = packet.sequence;
      }
      const trim = Math.max(0, cursor - packet.sequence);
      const payload = packet.payload.subarray(trim);
      chunks.push(payload);
      ranges.push({
        start: assembledOffset,
        end: assembledOffset + payload.length,
        time: packetTime(packet),
      });
      assembledOffset += payload.length;
      cursor += payload.length;
    }
    streams.push({ key, data: Buffer.concat(chunks), ranges });
  }
  return streams;
}

function streamTimeAtOffset(stream, offset) {
  for (const range of stream.ranges) {
    if (offset >= range.start && offset < range.end) return range.time;
  }
  return null;
}

function localSpiceEvents(file) {
  const packets = parseClassicPcapPackets(file);
  const events = [];
  for (const stream of reconstructTcpStreams(packets)) {
    const redqOffset = stream.data.indexOf(Buffer.from('REDQ', 'ascii'));
    if (redqOffset === 164) {
      let decoded;
      try {
        decoded = decodeLocalSpiceClientHandshake(stream.data);
      } catch (_) {
        continue;
      }
      const messages = decodeLocalSpiceClientDataMessages(decoded.rest, { maxFrames: 256 }).messages || [];
      const dataOffset = stream.data.length - decoded.rest.length;
      for (const msg of messages) {
        if (msg.channelPrefix !== 2) continue;
        if (!CLIENT_DISPLAY_LANDMARK_TYPES.has(msg.header.type)) continue;
        const absoluteOffset = dataOffset + msg.frameOffset;
        events.push({
          time: streamTimeAtOffset(stream, absoluteOffset),
          stream: stream.key,
          direction: 'local-client->spice-proxy',
          channelPrefix: msg.channelPrefix,
          type: msg.header.type,
          name: CLIENT_DISPLAY_LANDMARK_TYPES.get(msg.header.type),
          serial: msg.header.serial.toString(),
          size: msg.header.size,
          offset: absoluteOffset,
        });
      }
    } else if (redqOffset === 1) {
      let decoded;
      try {
        decoded = decodeLocalSpiceServerHandshake(stream.data);
      } catch (_) {
        continue;
      }
      const messages = decodeLocalSpiceServerDataMessages(decoded.rest, { maxMessages: 256 }).messages || [];
      const dataOffset = stream.data.length - decoded.rest.length;
      for (const msg of messages) {
        if (decoded.channelPrefix !== 2) continue;
        if (!SERVER_DISPLAY_LANDMARK_TYPES.has(msg.header.type)) continue;
        const absoluteOffset = dataOffset + msg.offset;
        events.push({
          time: streamTimeAtOffset(stream, absoluteOffset),
          stream: stream.key,
          direction: 'spice-proxy->local-client',
          channelPrefix: decoded.channelPrefix,
          type: msg.header.type,
          name: SERVER_DISPLAY_LANDMARK_TYPES.get(msg.header.type),
          serial: msg.header.serial.toString(),
          size: msg.header.size,
          offset: absoluteOffset,
        });
      }
    }
  }
  return events
    .filter((event) => event.time !== null)
    .sort((a, b) => a.time - b.time);
}

function summarizeWindow(events, center, windowSeconds, limit) {
  const from = center - windowSeconds;
  const to = center + windowSeconds;
  const matching = events.filter((event) => event.time >= from && event.time <= to);
  const counts = {};
  const bytes = {};
  for (const event of matching) {
    const key = `${event.direction}:${event.typeName}`;
    counts[key] = (counts[key] || 0) + 1;
    bytes[key] = (bytes[key] || 0) + event.payloadLength;
  }
  return {
    from: formatTimeNumber(from),
    to: formatTimeNumber(to),
    tunnelPackets: matching.length,
    counts,
    payloadBytes: bytes,
    tlsRecords: matching
      .filter((event) => event.tls)
      .slice(0, limit)
      .map((event) => ({
        time: formatTimeNumber(event.time),
        direction: event.direction,
        typeName: event.typeName,
        sequence16: event.sequence16,
        payloadLength: event.payloadLength,
        tls: event.tls,
      })),
    events: matching.slice(0, limit).map((event) => ({
      time: formatTimeNumber(event.time),
      direction: event.direction,
      typeName: event.typeName,
      sequence16: event.sequence16,
      payloadLength: event.payloadLength,
      word2Hex: event.word2Hex,
      word3Hex: event.word3Hex,
      word4Hex: event.word4Hex,
      tls: event.tls,
    })),
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const [cagFile, loopbackFile] = args._;
  if (!cagFile || !loopbackFile) usage();
  if (!Number.isFinite(args.windowMs) || args.windowMs <= 0) throw new Error('--window-ms must be positive');
  if (!Number.isInteger(args.limit) || args.limit <= 0) throw new Error('--limit must be a positive integer');

  const cag = cagTunnelEvents(cagFile);
  const spiceEvents = localSpiceEvents(loopbackFile);
  const windowSeconds = args.windowMs / 1000;

  console.log(JSON.stringify({
    cagFile,
    loopbackFile,
    remoteHost: cag.remoteHost,
    windowMs: args.windowMs,
    cagTunnelPackets: cag.events.length,
    spiceDisplayLandmarks: spiceEvents.map((event) => ({
      ...event,
      time: formatTimeNumber(event.time),
      cagWindow: summarizeWindow(cag.events, event.time, windowSeconds, args.limit),
    })),
  }, null, 2));
}

main();
