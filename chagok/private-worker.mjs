// Chagok personal 365-day fixed-audio player.
// New isolated Worker: does not modify Korean Voice production or the old three-year Sites app.
// Public, no-login personal-learning page; all audio files are fixed pretrained release assets.
const SOURCE = 'https://raw.githubusercontent.com/quddnr0107-lgtm/korean-voice/1ae8a868284b64263696b0ca24a038d0cbad51bc/chagok/';
const AUDIO = 'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/chagok-365-fixed-v1/';
const HEADERS = { 'Cache-Control':'public, max-age=60', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY' };

export default {
  async fetch(request,env) {
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
