/* Shared analytics view — used by the owner dashboard, admin view, the
   public results page and the results popup so all always match.
   Layout rule: nothing is truncated. Labels wrap onto their own line,
   bars sit underneath, long text breaks anywhere. */
(function () {
  const TYPE_LABELS = { multiple_choice: 'Multiple choice', rating: 'Rating', short_text: 'Short text', yes_no: 'Yes / No' };
  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }
  const num = n => Number(n).toLocaleString();
  const pctOf = (c, t) => (t > 0 ? Math.round((c / t) * 100) : 0);
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : (many || one + 's')}`;
  function fmtDate(iso, withTime) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) +
      (withTime ? ' · ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '');
  }
  function fmtDay(iso) {
    const d = new Date(iso + 'T12:00:00');
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function stat(label, value, sub, tone) {
    return `<div class="an-stat${tone ? ' an-stat-' + tone : ''}">
      <span class="an-stat-label">${esc(label)}</span>
      <span class="an-stat-num">${value}</span>
      ${sub ? `<span class="an-stat-sub">${sub}</span>` : ''}
    </div>`;
  }

  function bars(entries, total, opts) {
    opts = opts || {};
    const max = Math.max(1, ...entries.map(e => e[1]));
    return `<div class="an-bars">${entries.map(([label, count]) => {
      const pct = pctOf(count, total);
      const width = opts.relative ? Math.round((count / max) * 100) : pct;
      return `
        <div class="an-bar-row${count === 0 ? ' an-zero' : ''}">
          <div class="an-bar-top">
            <span class="an-bar-label">${esc(label)}</span>
            <span class="an-bar-count">${num(count)} <em>${pct}%</em></span>
          </div>
          <div class="an-bar-track"><div class="an-bar-fill" style="width:${width}%"></div></div>
        </div>`;
    }).join('')}</div>`;
  }

  // Options can be whole paragraphs ("Name - long blurb"). Show a short
  // name up front and keep the full text one tap away (never dropped).
  function splitLabel(label) {
    const t = String(label || '').trim();
    const m = t.match(/^(.{2,60}?)\s+[-\u2013\u2014:]\s+([\s\S]+)$/);
    if (m) return [m[1].trim(), m[2].trim()];
    if (t.length > 70) return [t.slice(0, 56).replace(/\s+\S*$/, '') + '\u2026', t];
    return [t, ''];
  }

  function rankedOptions(entries, total) {
    const voted = entries.filter(e => e[1] > 0);
    const none = entries.filter(e => e[1] === 0);
    const max = Math.max(1, ...voted.map(e => e[1]));
    const rows = voted.map(([label, count], i) => {
      const [name, desc] = splitLabel(label);
      const pct = pctOf(count, total);
      return `<div class="an-opt">
        <div class="an-bar-top">
          <span class="an-bar-label"><span class="an-rank">${i + 1}</span><span class="an-opt-name">${esc(name)}</span></span>
          <span class="an-bar-count">${num(count)} <em>${pct}%</em></span>
        </div>
        <div class="an-bar-track"><div class="an-bar-fill" style="width:${Math.round(count / max * 100)}%"></div></div>
        ${desc ? `<details class="an-desc"><summary>Full text</summary><p>${esc(desc)}</p></details>` : ''}
      </div>`;
    }).join('');
    const noneHtml = none.length ? `<button type="button" class="an-none-btn" aria-expanded="false" data-n="${none.length}">Show ${plural(none.length, 'option')} with no votes</button>
      <ul class="an-none hidden">${none.map(([l]) => { const [n, d] = splitLabel(l); return `<li>${esc(n)}${d ? `<span>${esc(d)}</span>` : ''}</li>`; }).join('')}</ul>` : '';
    return `<div class="an-bars an-ranked">${rows || '<p class="hint">No votes yet.</p>'}</div>${noneHtml}`;
  }

  function chips(items) {
    const list = items.filter(Boolean);
    return list.length ? `<div class="an-chips">${list.map(c => `<span class="an-chip">${c}</span>`).join('')}</div>` : '';
  }

  function questionCard(q, idx) {
    let body = '';
    let extra = [];
    let wide = false;
    if (q.response_rate != null) extra.push(`<b>${q.response_rate}%</b> answered`);
    if (q.skipped_count) extra.push(`<b>${num(q.skipped_count)}</b> skipped`);
    if (q.required) extra.push('Required');

    if (q.question_type === 'rating' && q.average_rating != null) {
      const dist = q.rating_counts || {};
      const entries = ['5', '4', '3', '2', '1'].map(k => [`${k} ${k === '1' ? 'star' : 'stars'}`, dist[k] || 0]);
      const pos = pctOf((dist['4'] || 0) + (dist['5'] || 0), q.response_count);
      const neg = pctOf((dist['1'] || 0) + (dist['2'] || 0), q.response_count);
      body = `
        <div class="an-big"><span class="an-big-num">${q.average_rating.toFixed(1)}</span><span class="an-big-sub">average out of 5</span></div>
        ${chips([
          q.median_rating != null ? `Median <b>${q.median_rating}</b>` : '',
          `<b>${pos}%</b> positive (4–5)`,
          `<b>${neg}%</b> negative (1–2)`,
        ])}
        ${bars(entries, q.response_count)}`;
    } else if (q.question_type === 'yes_no' && q.option_counts) {
      const yes = q.option_counts['Yes'] || 0;
      const no = q.option_counts['No'] || 0;
      const total = yes + no;
      const yesPct = pctOf(yes, total);
      body = total === 0 ? `<p class="hint">No responses yet.</p>` : `
        <div class="an-big"><span class="an-big-num">${yesPct}%</span><span class="an-big-sub">said yes</span></div>
        <div class="an-split" role="img" aria-label="${yes} yes, ${no} no">
          <div class="an-split-yes" style="flex:${yes || 0} 1 0"></div>
          <div class="an-split-no" style="flex:${no || 0} 1 0"></div>
        </div>
        <div class="an-split-legend"><span><i class="an-dot an-dot-yes"></i>Yes · ${num(yes)} (${yesPct}%)</span><span><i class="an-dot an-dot-no"></i>No · ${num(no)} (${100 - yesPct}%)</span></div>`;
    } else if (q.option_counts && Object.keys(q.option_counts).length > 0) {
      const entries = Object.entries(q.option_counts).sort((a, b) => b[1] - a[1]);
      body = `
        ${q.top_option && q.response_count ? `<p class="an-lead">Leading: <b>${esc(splitLabel(q.top_option)[0])}</b> (${q.top_option_pct}%)</p>` : ''}
        ${rankedOptions(entries, q.response_count)}`;
      wide = entries.length > 5;
    } else if (q.text_answers && q.text_answers.length > 0) {
      const shown = q.text_answers.slice(0, 5);
      const rest = q.text_answers.slice(5);
      const words = (q.common_words || []).map(w => `<span class="an-word">${esc(w.word)} <em>${w.count}</em></span>`).join('');
      if (q.avg_text_length) extra.push(`Avg <b>${num(q.avg_text_length)}</b> chars`);
      body = `
        ${words ? `<div class="an-words"><span class="an-mini-label">Common words</span>${words}</div>` : ''}
        <div class="an-texts">${shown.map(a => `<div class="an-text">${esc(a)}</div>`).join('')}</div>
        ${rest.length ? `<div class="an-texts an-more hidden">${rest.map(a => `<div class="an-text">${esc(a)}</div>`).join('')}</div>
        <button type="button" class="link-btn an-more-btn" data-count="${q.text_answers.length}">Show all ${num(q.text_answers.length)}</button>` : ''}`;
    } else {
      body = `<p class="hint">No responses yet.</p>`;
    }
    return `
      <div class="an-card${wide ? ' an-card-wide' : ''}">
        <div class="an-card-head">
          <span class="an-qnum">Q${idx + 1}</span>
          <div class="an-card-title">
            <span class="an-q">${esc(q.question_text)}</span>
            <span class="an-type">${TYPE_LABELS[q.question_type] || ''} · ${plural(q.response_count, 'answer')}</span>
          </div>
        </div>
        ${body}
        ${q.question_type === 'rating' || q.question_type === 'yes_no' || q.question_type === 'multiple_choice' || q.question_type === 'short_text' ? chips(extra) : ''}
      </div>`;
  }

  function timeline(days) {
    if (!days || !days.length) return '';
    const max = Math.max(1, ...days.map(d => d.count));
    const total = days.reduce((a, d) => a + d.count, 0);
    return `
      <div class="an-panel an-wide">
        <div class="an-panel-head"><span class="an-panel-title">Responses over the last 30 days</span><span class="an-panel-note">${plural(total, 'response')}</span></div>
        <div class="an-spark" role="img" aria-label="Responses per day, last 30 days">
          ${days.map(d => `<span class="an-spark-col" title="${fmtDay(d.date)}: ${d.count}"><i style="height:${d.count ? Math.max(6, Math.round((d.count / max) * 100)) : 2}%" class="${d.count ? '' : 'an-spark-zero'}"></i></span>`).join('')}
        </div>
        <div class="an-spark-axis"><span>${fmtDay(days[0].date)}</span><span>${fmtDay(days[Math.floor(days.length / 2)].date)}</span><span>${fmtDay(days[days.length - 1].date)}</span></div>
      </div>`;
  }

  function overviewHtml(data) {
    const o = data.overview;
    const total = data.total_submissions;
    if (!o) return '';
    const tiles = [stat('Total responses', num(total), o.last_submission_at ? `Latest ${fmtDate(o.last_submission_at)}` : 'None yet', total ? 'good' : '')];
    if (o.last_7_days != null) tiles.push(stat('Last 7 days', num(o.last_7_days), `${num(o.last_30_days)} in 30 days`));
    if (o.anonymous_count != null) tiles.push(stat('Anonymous', `${pctOf(o.anonymous_count, total)}%`, `${num(o.anonymous_count)} anonymous · ${num(o.named_count)} named`));
    if (o.avg_completion != null) tiles.push(stat('Avg. completion', `${o.avg_completion}%`, `of ${plural(o.question_count, 'question')}`));
    if (o.total_nominees) tiles.push(stat('Nominees', num(o.unique_nominees), `${plural(o.total_nominees, 'nomination')} total`));
    if (o.first_submission_at) tiles.push(stat('First response', '<span class="an-stat-date">' + fmtDate(o.first_submission_at) + '</span>', o.busiest_day ? `Busiest: ${fmtDay(o.busiest_day.date)} (${o.busiest_day.count})` : ''));

    let panels = '';
    if (o.per_day && total) panels += timeline(o.per_day);

    if (o.status_counts && total) {
      const sc = o.status_counts;
      panels += `<div class="an-panel">
        <div class="an-panel-head"><span class="an-panel-title">Review status</span><span class="an-panel-note">${pctOf(sc.done || 0, total)}% done</span></div>
        ${bars([['New', sc.new || 0], ['Reviewed', sc.reviewed || 0], ['Done', sc.done || 0]], total)}
      </div>`;
    }
    if (o.suggestion_count != null && total && (o.suggestion_count || o.nomination_count)) {
      panels += `<div class="an-panel">
        <div class="an-panel-head"><span class="an-panel-title">Submission type</span><span class="an-panel-note">can overlap</span></div>
        ${bars([['With a suggestion', o.suggestion_count], ['With a nomination', o.nomination_count], ['Answers only', Math.max(total - Math.max(o.suggestion_count, o.nomination_count), 0)]], total)}
      </div>`;
    }
    if (o.by_weekday && total) {
      const wmax = Math.max(1, ...o.by_weekday);
      panels += `<div class="an-panel">
        <div class="an-panel-head"><span class="an-panel-title">Busiest weekday</span><span class="an-panel-note">all time</span></div>
        <div class="an-week">${o.by_weekday.map((c, i) => `<div class="an-week-col"><span class="an-week-num">${c}</span><span class="an-week-bar"><i style="height:${c ? Math.max(6, Math.round((c / wmax) * 100)) : 2}%"></i></span><span class="an-week-day">${WEEKDAYS[i]}</span></div>`).join('')}</div>
      </div>`;
    }
    if (o.top_nominees && o.top_nominees.length) {
      panels += `<div class="an-panel">
        <div class="an-panel-head"><span class="an-panel-title">Most nominated</span><span class="an-panel-note">${plural(o.unique_nominees, 'person', 'people')}</span></div>
        ${bars(o.top_nominees.map(n => [n.name, n.count]), Math.max(...o.top_nominees.map(n => n.count)), { relative: true })}
      </div>`;
    }
    return `<div class="an-stats">${tiles.join('')}</div>${panels ? `<div class="an-panels">${panels}</div>` : ''}`;
  }

  window.renderAnalyticsCards = function (data, emptyMessage) {
    const hasQ = data.questions && data.questions.length > 0;
    return `<div class="an-root">
      ${overviewHtml(data)}
      ${hasQ
        ? `<h2 class="an-section">Question breakdown</h2><div class="an-grid">${data.questions.map(questionCard).join('')}</div>`
        : `<div class="empty-state">${esc(emptyMessage || 'This form has no questions yet.')}</div>`}
    </div>`;
  };

  window.bindAnalyticsCards = function (root) {
    root.querySelectorAll('.an-none-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const list = btn.nextElementSibling;
        const hidden = list.classList.toggle('hidden');
        btn.setAttribute('aria-expanded', String(!hidden));
        const n = Number(btn.dataset.n);
        btn.textContent = hidden ? `Show ${plural(n, 'option')} with no votes` : `Hide ${plural(n, 'option')} with no votes`;
      });
    });
    root.querySelectorAll('.an-more-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const more = btn.previousElementSibling;
        const hidden = more.classList.toggle('hidden');
        btn.textContent = hidden ? `Show all ${btn.dataset.count}` : 'Show fewer';
      });
    });
  };
})();
