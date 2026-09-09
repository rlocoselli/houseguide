import React,{useEffect,useRef,useState} from 'react';
let loader;
function loadGoogle(){
  if(window.google?.accounts?.id)return Promise.resolve();
  if(!loader)loader=new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src='https://accounts.google.com/gsi/client';script.async=true;script.defer=true;
    script.onload=resolve;script.onerror=()=>{loader=null;script.remove();reject(Error('google_unavailable'))};document.head.appendChild(script);
  });
  return loader;
}
export default function GoogleLogin({t,request,onSuccess,acceptTerms=false,endpoint="auth/google",onCredential}){
  const container=useRef(null), callback=useRef(onSuccess), [error,setError]=useState(''), [busy,setBusy]=useState(false);
  callback.current=onSuccess;
  const options=useRef({acceptTerms,endpoint,onCredential});options.current={acceptTerms,endpoint,onCredential};
  useEffect(()=>{
    let active=true;
    request('config').then(async config=>{
      if(!config.googleClientId||!active)return;
      await loadGoogle();if(!active)return;
      window.google.accounts.id.initialize({client_id:config.googleClientId,auto_select:false,callback:async({credential})=>{
        if(!active)return;setBusy(true);setError('');
        try{if(options.current.onCredential){options.current.onCredential(credential);return}const user=await request(options.current.endpoint,'POST',{credential,acceptTerms:options.current.acceptTerms});if(active)await callback.current(user)}
        catch(e){if(active)setError(e.message)}finally{if(active)setBusy(false)}
      }});
      window.google.accounts.id.renderButton(container.current,{theme:'outline',size:'large',width:300,text:'continue_with',locale:document.documentElement.lang});
    }).catch(()=>{if(active)setError('google_unavailable')});
    return()=>{active=false};
  },[]);
  return <div className="google-login"><div ref={container}/>{busy&&<p role="status">{t.loading}</p>}{error&&<p role="alert" className="error">{t[error]||t.error}</p>}</div>
}
