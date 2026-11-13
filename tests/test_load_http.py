import httpx
from scripts.load_http import exercise, percentile, CONCURRENCY, REQUESTS


def test_summary_keeps_business_rejection_and_network_failure_separate():
    class Client:
        def post(self, path, json):
            if json == 'network': raise httpx.ConnectError('synthetic')
            if json == 'sold': return httpx.Response(409, json={'error':{'code':'INSUFFICIENT_STOCK'}})
            return httpx.Response(201, json={'id':'one-order'})
    result=exercise(Client(),'/orders',['ok','ok','sold','network'],1)
    assert result['status_counts']=={'201':2,'409':1,'transport_or_decode_error':1}
    assert result['error_codes']=={'INSUFFICIENT_STOCK':1,'ConnectError':1}
    assert result['distinct_returned_order_ids']==1
    assert result['latency_ms']['p50']<=result['latency_ms']['p95']<=result['latency_ms']['p99']


def test_fixed_small_load_and_nearest_rank_percentiles():
    assert CONCURRENCY==(1,5,20) and REQUESTS==60
    assert percentile([5,1,4,2,3],.5)==3
    assert percentile([5,1,4,2,3],.95)==5
