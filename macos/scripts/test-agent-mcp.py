#!/usr/bin/env python3
"""Check the MCP discovery contract without issuing iPhone input. Pass a helper path."""
import json
import select
import subprocess
import sys

with subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.DEVNULL) as server:
    def send(message):
        server.stdin.write((json.dumps(message) + '\n').encode())
        server.stdin.flush()

    def call(identifier, method, params):
        send(dict(jsonrpc='2.0', id=identifier, method=method, params=params))
        while select.select([server.stdout], [], [], 15)[0]:
            response = json.loads(server.stdout.readline())
            if response.get('id') == identifier:
                assert 'error' not in response, response
                return response['result']
        raise TimeoutError(method)

    try:
        hello = call(1, 'initialize', dict(protocolVersion='2025-06-18', capabilities=(json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}),
                     clientInfo=dict(name='open-iphone-use-discovery-test', version='1')))
        assert 'ensure_session' in hello.get('instructions', ''), hello
        assert 'do something on their iPhone' in hello.get('instructions', ''), hello
        assert hello.get('serverInfo', {}).get('name') == 'open-iphone-use', hello
        send(dict(jsonrpc='2.0', method='notifications/initialized'))
        tools = {tool['name']: tool for tool in call(2, 'tools/list', {})['tools']}
        assert tools['ensure_session']['inputSchema']['required'] == []
        for tool in tools.values():
            assert 'device_id' not in tool['inputSchema']['required'], tool['name']
        for name in ['pointer_retry_tap', 'swipe', 'advance_feed']:
            assert name in tools
        assert 'profile' in tools['pointer_probe']['inputSchema']['properties']
        assert 'mode' in tools['advance_feed']['inputSchema']['properties']
        assert 'expected_frame_id' in tools['pointer_retry_tap']['inputSchema']['required']
        assert 'whole-screen'  in tools['pointer_tap']['description']
        print('PASS: new MCP client receives workflow and optional-device tool schemas.')
    finally:
        server.terminate()
        server.wait(timeout=5)
