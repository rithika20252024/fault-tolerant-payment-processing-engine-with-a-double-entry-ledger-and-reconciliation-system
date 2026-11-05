import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import exec from 'k6/execution';

const completion = new Trend('payment_completion_ms', true);
const completionFailures = new Rate('payment_completion_failures');
const completed = new Counter('payments_completed');
const base = __ENV.BASE_URL || 'http://localhost:18080';
const headers = {Authorization:`Bearer ${__ENV.API_TOKEN || 'local-demo-token-change-before-sharing'}`, 'Content-Type':'application/json'};
export const options = {
  scenarios: {payments:{executor:'constant-arrival-rate',rate:Number(__ENV.RATE || 20),timeUnit:'1s',
    duration:__ENV.DURATION || '30s',preAllocatedVUs:50,maxVUs:200}},
  thresholds:{payment_completion_failures:['rate<0.01'],payment_completion_ms:['p(95)<2000'],
    http_req_failed:['rate<0.01'],dropped_iterations:['count==0']}
};
export function setup() {
  const response=http.get(`${base}/health`);
  if(response.status!==200 || response.json().provider!=='simulator') throw new Error('Simulator required; never load-test Stripe');
  return {run:`${Date.now()}-${Math.random()}`};
}
export default function(data) {
  const key=`k6:${data.run}:${exec.scenario.iterationInTest}`;
  const response=http.post(`${base}/payments`,JSON.stringify({amount:1000,currency:'usd'}),
    {headers:{...headers,'Idempotency-Key':key},tags:{operation:'create'}});
  if(!check(response,{'accepted':r=>r.status===202})) {completionFailures.add(true);return;}
  const id=response.json().id;
  for(let i=0;i<240;i++) {
    const status=http.get(`${base}/payments/${id}`,{headers,tags:{operation:'poll'}});
    if(status.status===200 && status.json().status==='succeeded') {
      const body=status.json(); completion.add(Date.parse(body.completed_at)-Date.parse(body.created_at));
      completed.add(1);completionFailures.add(false);return;
    }
    sleep(.25);
  }
  completionFailures.add(true);
}
export function teardown() {
  const audit=http.get(`${base}/invariants`,{headers});
  if(audit.status!==200 || !audit.json().ok) throw new Error('Ledger audit failed');
}
