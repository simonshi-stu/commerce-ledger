"""Bounded, synthetic loopback HTTP experiment. No external target option."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import httpx

CONCURRENCY = (1, 5, 20)
REQUESTS = 60


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def exercise(client, path, payloads, workers):
    def request(payload):
        started = time.perf_counter()
        try:
            response = client.post(path, json=payload)
            body = response.json()
            return response.status_code, (time.perf_counter()-started)*1000, body.get('id'), body.get('error', {}).get('code')
        except (httpx.HTTPError, ValueError) as exc:
            return 'transport_or_decode_error', (time.perf_counter()-started)*1000, None, type(exc).__name__
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(request, payloads))
    elapsed = time.perf_counter()-started
    latencies = [r[1] for r in results]
    return {
        'concurrency': workers, 'requests': len(results), 'wall_seconds': round(elapsed, 4),
        'completed_requests_per_second': round(len(results)/elapsed, 2),
        'latency_ms': {k: round(percentile(latencies, p), 2) for k,p in [('p50', .5), ('p95', .95), ('p99', .99)]},
        'status_counts': dict(Counter(str(r[0]) for r in results)),
        'error_codes': dict(Counter(r[3] for r in results if r[3])),
        'distinct_returned_order_ids': len({r[2] for r in results if r[2]}),
    }


def run():
    root = Path(__file__).resolve().parents[1]
    report = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'synthetic_loopback_single_uvicorn_process_sqlite',
              'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'concurrency_levels': list(CONCURRENCY), 'requests_per_phase': REQUESTS,
              'claim': 'bounded correctness and observed latency, not production capacity or anti-DDoS', 'phases': []}
    with TemporaryDirectory(prefix='commerce-load-') as folder:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = os.environ.copy()
        env.pop('COMMERCE_DATABASE_URL', None)
        env.update(COMMERCE_DB=str(Path(folder)/'synthetic.db'), COMMERCE_API_KEY='load-synthetic-only',
                   COMMERCE_READ_API_KEY='load-synthetic-reader')
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
                                    '--host','127.0.0.1','--port',str(port),'--no-access-log'],
                                   cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False,
                              headers={'X-API-Key':env['COMMERCE_API_KEY']}, timeout=10,
                              limits=httpx.Limits(max_connections=20,max_keepalive_connections=20)) as client:
                deadline=time.monotonic()+15
                while True:
                    try:
                        if client.get('/health').status_code==200: break
                    except httpx.HTTPError: pass
                    if process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('local startup failed')
                    time.sleep(.1)
                for workers in CONCURRENCY:
                    for mode in ('unique_orders', 'same_key_retries', 'last_unit', 'repeated_cancel'):
                        sku=f'LOAD-{workers}-{mode.upper()}'
                        created=client.post('/products',json={'sku':sku,'name':'Synthetic load item','price_cents':1000})
                        assert created.status_code==201, created.text
                        pid=created.json()['id']
                        stock=REQUESTS if mode=='unique_orders' else 1
                        adjusted=client.post(f'/products/{pid}/stock-adjustments',json={
                            'delta':stock,'request_key':f'initial-{sku}','reason':'synthetic'})
                        assert adjusted.status_code==201
                        payloads=[{'request_key':f'load-{workers}-{mode}-{i if mode in ("unique_orders","last_unit") else 0}',
                                   'items':[{'product_id':pid,'quantity':1}]} for i in range(REQUESTS)]
                        path='/orders'
                        if mode=='repeated_cancel':
                            placed=client.post('/orders',json=payloads[0]); assert placed.status_code==201
                            path=f'/orders/{placed.json()["id"]}/cancel'
                            payloads=[None]*REQUESTS
                        phase=exercise(client,path,payloads,workers); phase['scenario']=mode
                        phase['remaining_stock']=client.get(f'/products/{pid}').json()['available']
                        expected_status={'201':REQUESTS} if mode in ('unique_orders','same_key_retries') else (
                            {'201':1,'409':REQUESTS-1} if mode=='last_unit' else {'200':REQUESTS})
                        expected_ids=REQUESTS if mode=='unique_orders' else 1
                        phase['passed']=(phase['status_counts']==expected_status
                                         and phase['distinct_returned_order_ids']==expected_ids
                                         and phase['remaining_stock']==(1 if mode=='repeated_cancel' else 0)
                                         and (mode!='last_unit' or phase['error_codes']=={'INSUFFICIENT_STOCK':REQUESTS-1}))
                        report['phases'].append(phase)
                report['integrity']=client.get('/integrity').json()
                report['passed']=all(p['passed'] for p in report['phases']) and report['integrity']['healthy'] and report['integrity']['orders_checked']==len(CONCURRENCY)*(REQUESTS+3)
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            report['server_stopped']=process.poll() is not None
    return report


if __name__=='__main__':
    result=run()
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['passed'] and result['server_stopped'] else 1)
