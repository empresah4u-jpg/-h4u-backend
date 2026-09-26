"""Counter-offer acceptance uses actual ownership and a locked offer version."""
from datetime import datetime,timezone,timedelta
from tests.test_identity_auth import identity_case,headers
from tests.test_commercial import flow,request as create_request
from app.routers import partner_responses as pr


def prepare(case):
    req=create_request(case['flow'])
    candidate=req['candidates'][0]['request_partner_id']
    pr.respond_to_request(pr.PartnerResponseCreate(request_partner_id=candidate,action='counter_offer',proposed_price=120,proposed_currency='PEN'))
    tourist=case['user'](role='tourist')
    return candidate,headers(case['token'](tourist)),req


def test_tourist_accepts_own_offer_and_cannot_repeat(identity_case):
    c=identity_case; candidate,h,req=prepare(c)
    url='/partner-responses/'+candidate
    offer=c['client'].get(url+'/counter-offer',headers=h)
    assert offer.status_code==200 and float(offer.json()['price'])==120
    result=c['client'].post(url+'/accept-counter-offer',headers=h,json={'expected_version':offer.json()['version']})
    assert result.status_code==200 and result.json()['winner']
    assert c['client'].post(url+'/accept-counter-offer',headers=h,json={'expected_version':offer.json()['version']}).status_code==409
    reservation=c['client'].post('/reservations',headers=h,json={'service_request_code':req['code']})
    assert reservation.status_code==201 and reservation.json()['agreed_price']==120


def test_stale_offer_rejected(identity_case):
    c=identity_case; candidate,h,_=prepare(c)
    stale=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    assert c['client'].post('/partner-responses/'+candidate+'/accept-counter-offer',headers=h,json={'expected_version':stale}).status_code==409
    assert c['db'].execute('SELECT status FROM request_partners WHERE id=%s',(candidate,)).fetchone()[0]=='counter_offered'


def test_other_tourist_and_partner_cannot_accept(identity_case):
    c=identity_case; candidate,_,_=prepare(c)
    other=c['db'].execute('INSERT INTO travelers DEFAULT VALUES RETURNING id').fetchone()[0]
    for user in (c['user'](role='tourist',owner=other),c['user'](role='partner')):
        h=headers(c['token'](user))
        assert c['client'].get('/partner-responses/'+candidate+'/counter-offer',headers=h).status_code==403
        assert c['client'].post('/partner-responses/'+candidate+'/accept-counter-offer',headers=h,json={'expected_version':datetime.now(timezone.utc).isoformat()}).status_code==403
