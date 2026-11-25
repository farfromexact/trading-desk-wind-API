import React, {useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';

// Render outside cards so overflow and narrow layouts never clip the explanation.
export function MetricHelp({label, children}) {
  const id=useId(), button=useRef(null), bubble=useRef(null);
  const [open,setOpen]=useState(false), [pinned,setPinned]=useState(false);
  const [position,setPosition]=useState({left:8,top:8,visibility:'hidden'});
  const close=()=>{setOpen(false);setPinned(false);};
  useLayoutEffect(()=>{
    if(!open)return;
    const anchor=button.current.getBoundingClientRect(), box=bubble.current.getBoundingClientRect();
    const top=anchor.bottom+box.height+12<window.innerHeight?anchor.bottom+8:anchor.top-box.height-8;
    setPosition({left:Math.max(8,Math.min(anchor.left,window.innerWidth-box.width-8)),top:Math.max(8,top),visibility:'visible'});
  },[open,children]);
  useEffect(()=>{
    if(!open)return;
    const outside=e=>{if(!button.current?.contains(e.target)&&!bubble.current?.contains(e.target))close();};
    const escape=e=>{if(e.key==='Escape')close();};
    // Closing on layout changes is preferable to a tooltip pointing at an old position.
    document.addEventListener('pointerdown',outside);
    document.addEventListener('keydown',escape);
    window.addEventListener('resize',close);
    window.addEventListener('scroll',close,true);
    return()=>{
      document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);
      window.removeEventListener('resize',close);window.removeEventListener('scroll',close,true);
    };
  },[open]);
  return <span className="mc-help-anchor" onMouseEnter={()=>setOpen(true)} onMouseLeave={()=>{if(!pinned)setOpen(false);}}>
    <button ref={button} type="button" className="mc-help-button" aria-label={label+'说明'}
      aria-expanded={open} aria-describedby={open?id:undefined}
      onFocus={()=>setOpen(true)} onBlur={()=>{if(!pinned)setOpen(false);}}
      onClick={()=>{setPinned(!pinned);setOpen(!pinned);}}>?</button>
    {open&&createPortal(<div ref={bubble} id={id} role="tooltip" className="mc-help-bubble" style={position}
      onMouseEnter={()=>setOpen(true)} onMouseLeave={()=>{if(!pinned)setOpen(false);}}>
      <strong>{label}</strong><p>{children}</p><small>点击 ? 固定说明 · 再点、Esc 或点空白处关闭</small>
    </div>,button.current?.closest('dialog[open]') || document.body)}
  </span>;
}
