import React,{useState,useEffect}from'react';
import {legalText}from'./legal';
import{languages}from'./i18n';
export default function LegalPage({lang,setLang}){
 const [info,setInfo]=useState(null),[error,setError]=useState(false),[section,setSection]=useState(new URLSearchParams(location.search).get('doc')||'privacy');const copy=legalText[lang]||legalText.pt;
 useEffect(()=>{fetch('/api/legal').then(r=>{if(!r.ok)throw Error();return r.json()}).then(setInfo).catch(()=>setError(true))},[]);
 const replace=text=>text.replace(/\{(\w+)\}/g,(_,k)=>info?.[k]||'[…]');
 return <main className="legal-page"><a href="/">← House Guide</a><select aria-label="Language" value={lang} onChange={e=>setLang(e.target.value)}>{Object.entries(languages).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select><h1>{copy.title}</h1>{info?.draft&&<p className="legal-draft">{copy.draft}</p>}{error&&<p role="alert">Não foi possível carregar / Unable to load</p>}<nav>{['privacy','terms','cookies','processing'].map(k=><button className={section===k?'active':''} key={k} onClick={()=>setSection(k)}>{copy[k]}</button>)}</nav><h2>{copy[section]||copy.privacy}</h2>{(copy.body[section]||copy.body.privacy).map(([title,body])=><section key={title}><h3>{title}</h3><p>{replace(body)}</p></section>)}<p><a href="https://commission.europa.eu/law/law-topic/data-protection/information-individuals_en">RGPD / GDPR</a> · <a href="https://www.gov.br/anpd/pt-br/assuntos/titular-de-dados-1/direito-dos-titulares">LGPD / ANPD</a> · <a href="https://www.cnil.fr/fr/mes-demarches/les-droits-pour-maitriser-vos-donnees-personnelles">CNIL</a></p></main>
}
