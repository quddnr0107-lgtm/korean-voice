// Chagok personal 365-day fixed-audio player.
// New isolated Worker: does not modify Korean Voice production or the old three-year Sites app.
// Basic authentication required on every endpoint. Password is stored only as a Worker secret.
const SOURCE = 'https://raw.githubusercontent.com/quddnr0107-lgtm/korean-voice/1ae8a868284b64263696b0ca24a038d0cbad51bc/chagok/';
const AUDIO = 'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/chagok-365-fixed-v1/';
const PASSWORD_SHA256 = 'ee1e7c29b4473412ca59e413512c725db950be94982232d1789a73ac6c40b962';
const HEADERS = { 'Cache-Control':'private, no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY' };

async function same(a,b) {
  const enc = new TextEncoder();
  const [x,y] = await Promise.all([crypto.subtle.digest('SHA-256',enc.encode(a)),crypto.subtle.digest('SHA-256',enc.encode(b))]);
  const xa=new Uint8Array(x),ya=new Uint8Array(y);
  let v=0;for(let i=0;i<xa.length;i++)v|=xa[i]^ya[i];return v===0;
}
async function authorized(req, env) {
  const header=req.headers.get('authorization')||'';
  if(!header.startsWith('Basic '))return false;
  let decoded;try{decoded=atob(header.slice(6))}catch{return false}
  const colon=decoded.indexOf(':');
  if(colon<0)return false;
  if(!await same(decoded.slice(0,colon),'study'))return false;
  const pw = decoded.slice(colon+1);
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(pw)))).map(b=>b.toString(16).padStart(2,'0')).join('');
  return await same(hash,PASSWORD_SHA256);
}
export default {
  async fetch(request,env) {
    if(!await authorized(request,env))return new Response('Authorization required. Log in with your private study credentials.',{status:401,headers:{...HEADERS,'WWW-Authenticate':'Basic realm="Chagok 365 personal", charset="UTF-8"'}});
    if(request.method!=='GET'&&request.method!=='HEAD')return new Response('Method not allowed',{status:405,headers:HEADERS});
    const path=new URL(request.url).pathname;
    if(path==='/'||path==='/index.html') {
      const upstream=await fetch(SOURCE+'private-player.html',{cf:{cacheTtl:300}});
      if(!upstream.ok)return new Response('Player not available',{status:502,headers:HEADERS});
      return new Response(request.method==='HEAD'?null:upstream.body,{status:200,headers:{...HEADERS,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; media-src 'self' https://github.com https://release-assets.githubusercontent.com https://objects.githubusercontent.com; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}});
    }
    if(path==='/transcript'){
      const upstream=await fetch(SOURCE+'365-fixed-transcript.txt',{cf:{cacheTtl:3600}});
      if(!upstream.ok)return new Response('Transcript not available',{status:502,headers:HEADERS});
      return new Response(request.method==='HEAD'?null:upstream.body,{status:200,headers:{...HEADERS,'Content-Type':'text/plain; charset=utf-8'}});
    }
    if(path==='/media/learn'||path==='/media/listen'){
      const file=path.endsWith('learn')?'chagok-365-learn.mp3':'chagok-365-listen.mp3';
      return Response.redirect(AUDIO+file,302);
    }
    if(path==='/health')return Response.json({status:'ok',course:'365-day-fixed',sentences:389,tracks:2},{headers:HEADERS});
    return new Response('Not found',{status:404,headers:HEADERS});
  }
};
