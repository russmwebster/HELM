"""HELM russ-scan: independent, pre-reveal directional pick capture.

Shows the latest scan's candidates as a comparative grid of RAW FACTS ONLY.
HELM's read (bias / strategy / fit) is held server-side and only returned
after the user commits their picks, then shown side by side. Writes
russ_intent and user_bias_override onto the latest batch's signals rows.
"""
import json
import datetime

RAW_COLS = ["ticker", "spot_price", "ema_20", "sma_50", "sma_200",
            "rsi_14", "atr_14", "iv_rank", "iv_percentile"]


def _latest(conn):
    r = conn.execute("SELECT MAX(generated_at) FROM signals").fetchone()
    return r[0] if r and r[0] else None


def _rows(conn, gen):
    q = "SELECT " + ",".join(RAW_COLS) + " FROM signals WHERE generated_at=? ORDER BY ticker"
    res = []
    for row in conn.execute(q, (gen,)):
        res.append({RAW_COLS[i]: row[i] for i in range(len(RAW_COLS))})
    return res


def render(conn, path=None):
    gen = _latest(conn)
    if not gen:
        return ("<html><body style='font-family:system-ui;padding:48px;"
                "background:#0f1115;color:#e6e6e6'><h2>No scan data yet</h2>"
                "<p>Run <code>helm scan --blind</code>, then reload.</p></body></html>")
    rows = _rows(conn, gen)
    payload = json.dumps({"generated_at": gen, "rows": rows})
    return (_PAGE.replace("__DATA__", payload)
                 .replace("__GEN__", gen)
                 .replace("__N__", str(len(rows))))


def commit(conn, payload):
    gen = payload.get("generated_at")
    picks = payload.get("picks", {})
    now = datetime.datetime.now().isoformat(timespec="seconds")
    m = {"bull": ("OPEN", "BULLISH"), "bear": ("OPEN", "BEARISH"), "pass": ("SKIP", None)}
    n = 0
    for tk, choice in picks.items():
        intent, bias = m.get(choice, ("SKIP", None))
        conn.execute("UPDATE signals SET russ_intent=?, user_bias_override=?, "
                     "russ_intent_at=? WHERE generated_at=? AND ticker=?",
                     (intent, bias, now, gen, tk))
        n += 1
    conn.commit()
    reveal = {}
    for tk in picks:
        r = conn.execute("SELECT auto_bias, top_strategy, top_fit, auto_bias_reasoning "
                         "FROM signals WHERE generated_at=? AND ticker=?", (gen, tk)).fetchone()
        if r:
            reveal[tk] = {"auto_bias": r[0], "top_strategy": r[1],
                          "top_fit": r[2], "reasoning": r[3]}
    return {"saved": n, "reveal": reveal}


_PAGE = """
<!doctype html>
<html><head><meta charset='utf-8'><title>HELM russ-scan</title>
<style>
 body{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:16px 24px;border-bottom:1px solid #242832;display:flex;align-items:center;gap:16px;position:sticky;top:0;background:#0f1115;z-index:5}
 header h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.02em}
 .sub{color:#8b93a7;font-size:12px}
 .spacer{flex:1}
 button.commit{background:#3b82f6;color:#fff;border:0;padding:9px 16px;border-radius:7px;font-size:13px;cursor:pointer}
 button.commit:disabled{opacity:.45;cursor:default}
 table{border-collapse:collapse;width:100%;font-size:12.5px}
 th,td{padding:7px 11px;text-align:right;border-bottom:1px solid #1b1f29;white-space:nowrap}
 th{position:sticky;top:57px;background:#141821;color:#8b93a7;font-weight:500;z-index:4}
 th.l,td.l{text-align:left}
 td.tk{font-weight:600;color:#fff}
 tr:hover td{background:#161b24}
 .pick{display:inline-flex;gap:4px}
 .pick button{border:1px solid #2b3140;background:transparent;color:#8b93a7;width:30px;height:26px;border-radius:6px;cursor:pointer;font-size:13px}
 .pick button.bull.on{background:#15803d;border-color:#15803d;color:#fff}
 .pick button.bear.on{background:#b91c1c;border-color:#b91c1c;color:#fff}
 .pick button.pass.on{background:#374151;border-color:#374151;color:#fff}
 .muted{color:#565d6e}
 .agree{color:#22c55e}
 .disagree{color:#f59e0b}
 .reveal-col{display:none}
 body.revealed .reveal-col{display:table-cell}
 #summary{padding:10px 24px;color:#cdd3e0;font-size:12.5px;border-bottom:1px solid #242832;display:none}
 body.revealed #summary{display:block}
</style></head>
<body>
<header>
 <h1>russ-scan</h1>
 <span class='sub'>__N__ candidates &middot; read sealed &middot; <span id='gen'></span></span>
 <span class='spacer'></span>
 <span class='sub' id='count'>0 picks marked</span>
 <button class='commit' id='commitBtn'>Commit &amp; reveal</button>
</header>
<div id='summary'></div>
<table id='grid'><thead></thead><tbody></tbody></table>
<script>
var DATA = __DATA__;
var picks = {};
function fmt(v,d){ if(v===null||v===undefined||v!==v) return '<span class=muted>--</span>'; var n=Number(v); return (d===0)?Math.round(n).toString():n.toFixed(d); }
function pct(p,base){ if(p===null||base===null||p!==p||base!==base||!base) return '<span class=muted>--</span>'; var x=(p/base-1)*100; var c=x>=0?'#22c55e':'#ef4444'; return '<span style=color:'+c+'>'+(x>=0?'+':'')+x.toFixed(1)+'%</span>'; }
function rsiCell(v){ if(v===null||v!==v) return '<span class=muted>--</span>'; var c='#e6e6e6'; if(v<=30)c='#22c55e'; else if(v>=70)c='#ef4444'; return '<span style=color:'+c+'>'+v.toFixed(0)+'</span>'; }
function head(){
  var h='<tr><th class=l>Ticker</th><th>Price</th><th>vs20</th><th>vs50</th><th>vs200</th><th>RSI</th><th>ATR</th><th>IVR</th><th>IVP</th><th class=l>Your read</th><th class="l reveal-col">HELM</th></tr>';
  document.querySelector('#grid thead').innerHTML=h;
}
function rowHtml(r){
  var t=r.ticker;
  return '<tr data-tk="'+t+'">'+
    '<td class="l tk">'+t+'</td>'+
    '<td>'+fmt(r.spot_price,2)+'</td>'+
    '<td>'+pct(r.spot_price,r.ema_20)+'</td>'+
    '<td>'+pct(r.spot_price,r.sma_50)+'</td>'+
    '<td>'+pct(r.spot_price,r.sma_200)+'</td>'+
    '<td>'+rsiCell(r.rsi_14)+'</td>'+
    '<td>'+fmt(r.atr_14,2)+'</td>'+
    
    '<td>'+fmt(r.iv_rank,0)+'</td>'+
    '<td>'+fmt(r.iv_percentile,0)+'</td>'+
    '<td class=l><span class=pick>'+
      '<button class="bull" data-c="bull">B</button>'+
      '<button class="bear" data-c="bear">S</button>'+
      '<button class="pass on" data-c="pass">.</button>'+
    '</span></td>'+
    '<td class="l reveal-col" id="rev_'+t+'"></td></tr>';
}
function updateCount(){
  var n=0; for(var k in picks){ if(picks[k]&&picks[k]!=='pass') n++; }
  document.getElementById('count').textContent=n+' picks marked';
}
function doReveal(res){
  document.body.classList.add('revealed');
  var rv=res.reveal||{}; var made=0, agree=0, dis=0;
  for(var tk in rv){
    var hed=rv[tk]; var cell=document.getElementById('rev_'+tk); if(!cell) continue;
    var uc=picks[tk]; var hb=(hed.auto_bias||'');
    var match=(uc==='bull'&&hb.indexOf('BULL')>=0)||(uc==='bear'&&hb.indexOf('BEAR')>=0);
    var conflict=(uc==='bull'&&hb.indexOf('BEAR')>=0)||(uc==='bear'&&hb.indexOf('BULL')>=0);
    var cls=match?'agree':(conflict?'disagree':'muted');
    cell.innerHTML='<span class='+cls+'>'+(hed.auto_bias||'--')+' &middot; '+(hed.top_strategy||'--')+' &middot; '+(hed.top_fit||'--')+'</span>';
    if(uc&&uc!=='pass'){ made++; if(match)agree++; if(conflict)dis++; }
  }
  document.getElementById('summary').innerHTML='Revealed. '+made+' directional picks &middot; agreed with HELM on '+agree+' &middot; conflicted on '+dis+'.';
  var b=document.getElementById('commitBtn'); b.textContent='Committed'; b.disabled=true;
}
function init(){
  document.getElementById('gen').textContent=(DATA.generated_at||'').replace('T',' ').substring(0,16);
  head();
  var tb=document.querySelector('#grid tbody'); var html='';
  for(var i=0;i<DATA.rows.length;i++){ html+=rowHtml(DATA.rows[i]); }
  tb.innerHTML=html;
  tb.addEventListener('click', function(e){
    var b=e.target; if(b.tagName!=='BUTTON'||!b.getAttribute('data-c')) return;
    var tr=b.parentNode.parentNode.parentNode; var tk=tr.getAttribute('data-tk'); var c=b.getAttribute('data-c');
    picks[tk]=c;
    var bs=tr.querySelectorAll('.pick button'); for(var j=0;j<bs.length;j++) bs[j].classList.remove('on');
    b.classList.add('on'); updateCount();
  });
  document.getElementById('commitBtn').addEventListener('click', function(){
    var btn=this; btn.disabled=true; btn.textContent='Saving...';
    var trs=document.querySelectorAll('#grid tbody tr');
    for(var i=0;i<trs.length;i++){ var tk=trs[i].getAttribute('data-tk'); if(!picks[tk]) picks[tk]='pass'; }
    fetch('/russ-scan/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({generated_at:DATA.generated_at,picks:picks})})
      .then(function(r){return r.json();}).then(function(res){doReveal(res);})
      .catch(function(e){btn.textContent='Error';});
  });
}
init();
</script></body></html>
"""
