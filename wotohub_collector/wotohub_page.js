/* Page-context helpers.  Returned values are plain JSON for Playwright. */
() => {
  const visible = e => !!e && (() => { const s=getComputedStyle(e), r=e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width>0 && r.height>0; })();
  const text = e => (e?.innerText || e?.textContent || '').trim();
  const plain = x => x && typeof x === 'object' && Object.prototype.toString.call(x) === '[object Object]';
  const handle = x => String(x || '').trim().replace(/^@/, '');
  const rowScore = rows => !Array.isArray(rows) ? -1 : rows.slice(0,50).reduce((n,r) => n + (plain(r) ? (r.username ? 8 : 0) + (r.handle || r.uniqueId || r.account ? 5 : 0) + (r.nickname ? 2 : 0) + (r.platform ? 1 : 0) : 0), 0);
  function walk(root, out, seen, depth=0) {
    if (!root || typeof root !== 'object' || seen.has(root) || depth > 6) return;
    seen.add(root);
    if (Array.isArray(root)) { if (rowScore(root)>0) out.push(root); for (const x of root.slice(0,80)) walk(x,out,seen,depth+1); return; }
    for (const key of Object.keys(root)) {
      if (/^\$|^_|parent|root|children|vnode|watcher|observer/i.test(key)) continue;
      let v; try { v=root[key]; } catch (_) { continue; }
      if (typeof v !== 'function' && (Array.isArray(v) || /list|data|rows|table|result|item/i.test(key))) walk(v,out,seen,depth+1);
    }
  }
  function bestVueRows() {
    const lists=[], seen=new WeakSet();
    for (const el of document.querySelectorAll('*')) {
      const v=el.__vue__ || el.__vueParentComponent;
      if (!v) continue;
      walk(v.$data || v.data || v,lists,seen); walk(v.$props || v.props,lists,seen);
    }
    return lists.sort((a,b)=>rowScore(b)-rowScore(a))[0] || [];
  }
  const vueRows=bestVueRows();
  const handles=[...new Set(vueRows.map(r=>handle(r?.username||r?.handle||r?.uniqueId||r?.account||r?.screenName)).filter(Boolean))];
  const active=[...document.querySelectorAll('.el-pagination .el-pager li.number.active,.el-pagination .is-active,.ant-pagination-item-active,.ivu-page-item-active')].find(visible);
  const next=[...document.querySelectorAll('.el-pagination .btn-next,.ant-pagination-next,.ivu-page-next,[aria-label="Next"],[aria-label="Next Page"]')].find(visible);
  return { handles, vueRowCount:vueRows.length, activePage: Number(text(active)) || null,
    nextDisabled: !next || !!next.disabled || next.getAttribute('aria-disabled')==='true' || /disabled|is-disabled/i.test(next.className||''),
    firstHandle: handles[0] || null, bodyText: document.body?.innerText?.slice(0,3000) || '' };
}
