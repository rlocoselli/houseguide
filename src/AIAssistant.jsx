import React, {useState} from 'react';
import {Sparkles, Languages, Check, X} from 'lucide-react';
import {languages} from './i18n';

export default function AIAssistant({t, lang, content, onApply, request, onBusy}) {
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [proposal,setProposal]=useState(null);
  const [context,setContext]=useState(''), [overwrite,setOverwrite]=useState(false);
  async function generate(kind) {
    setError('');setProposal(null);
    const source=Object.fromEntries(Object.entries(content[lang]||{}).filter(([,v])=>v.trim()));
    if(kind==='translate'&&!Object.keys(source).length){setError(t.aiSourceEmpty);return}
    setBusy(true);onBusy(true);
    try {
      const data=kind==='translate'?{source,targets:Object.keys(languages).filter(l=>l!==lang)}:{lang,context,existing:content[lang]?.rules||''};
      const result=await request('ai/'+kind,'POST',data);
      setProposal(kind==='translate'?{kind,translations:result.translations}:{kind,lang,fields:result});
    } catch(e) {setError(t[e.message]||t.error)}
    finally {setBusy(false);onBusy(false)}
  }
  function apply() {
    if(!proposal)return;
    const next=structuredClone(content);
    if(proposal.kind==='rules'&&proposal.fields&&typeof proposal.fields==='object')next[proposal.lang]={...next[proposal.lang],...proposal.fields};
    else for(const [l,fields] of Object.entries(proposal.translations)) {
      next[l]={...next[l]};
      for(const [key,value] of Object.entries(fields))if(overwrite||!next[l][key]?.trim())next[l][key]=value;
    }
    onApply(next);setProposal(null);
  }
  return <section className="ai-assistant">
    <h3><Sparkles size={19}/>{t.aiTitle}</h3><p>{t.aiDescription}</p>
    <div className="ai-actions"><button type="button" className="secondary" disabled={busy} onClick={()=>generate('translate')}><Languages size={16}/>{t.aiTranslate}</button><button type="button" className="secondary" disabled={busy} onClick={()=>generate('rules')}><Sparkles size={16}/>{t.aiRules}</button></div>
    <label>{t.aiContext}<textarea disabled={busy} maxLength={4000} value={context} onChange={e=>setContext(e.target.value)} placeholder={t.aiContextHint} rows={2}/></label>
    <small>{t.aiPrivacy}</small>
    {busy&&<p role="status">{t.aiWorking}</p>}{error&&<p className="error" role="alert">{error}</p>}
    {proposal&&<div className="ai-proposal"><h4>{t.aiReview}</h4><p>{t.aiReviewHint}</p>
      {proposal.kind==='translate'?<>{Object.entries(proposal.translations||{}).map(([l,fields])=><details key={l}><summary>{languages[l]}</summary>{Object.entries(fields||{}).map(([key,value])=><div key={key}><strong>{t[key]||key}</strong><p>{value}</p></div>)}</details>)}<label className="checkbox"><input type="checkbox" checked={overwrite} onChange={e=>setOverwrite(e.target.checked)}/>{t.aiOverwrite}</label></>:<>{Object.entries(proposal.fields||{}).map(([key,value])=><div key={key}><strong>{t[key]||key}</strong><p>{value}</p></div>)}</>}
      <div className="ai-actions"><button type="button" className="primary" onClick={apply}><Check size={16}/>{t.aiApply}</button><button type="button" className="secondary" onClick={()=>setProposal(null)}><X size={16}/>{t.cancel}</button></div>
    </div>}
  </section>
}
