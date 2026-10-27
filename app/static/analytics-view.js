/* Shared analytics cards — used by the owner dashboard and the public
   results page so both always look identical. */
(function () {
  const TYPE_LABELS = { multiple_choice: 'Multiple choice', rating: 'Rating', short_text: 'Short text', yes_no: 'Yes / No' };

  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function bars(entries, total) {
    return `<div class="an-bars">${entries.map(([label, count]) => {
      const pct = total > 0 ? Math.round((count / total) * 100) : 0;
      return `
        <div class="an-bar-row">
          <span class="an-bar-label">${esc(label)}</span>
          <div class="an-bar-track"><div class="an-bar-fill" style="width:${pct}%"></div></div>
          <span class="an-bar-count">${count} (${pct}%)</span>
        </div>`;
    }).join('')}</div>`;
  }

  function questionCard(q) {
    let body = '';
    if (q.question_type === 'rating' && q.average_rating !== null && q.average_rating !== undefined) {
      const dist = q.rating_counts || {};
      const entries = ['5', '4', '3', '2', '1'].map(k => [k, dist[k] || 0]);
      body = `
        <div class="an-big"><span class="an-big-num">${q.average_rating.toFixed(1)}</span><span class="an-big-sub">average out of 5</span></div>
        ${q.rating_counts ? bars(entries, q.response_count) : ''}`;
    } else if (q.question_type === 'yes_no' && q.option_counts) {
      const yes = q.option_counts['Yes'] || 0;
      const no = q.option_counts['No'] || 0;
      const total = yes + no;
      const yesPct = total ? Math.round((yes / total) * 100) : 0;
      body = total === 0 ? `<p class="hint">No responses yet.</p>` : `
        <div class="an-split">
          <div class="an-split-yes" style="width:${Math.max(yesPct, yes ? 12 : 0)}%">${yes ? `Yes · ${yes} (${yesPct}%)` : ''}</div>
          <div class="an-split-no">${no ? `No · ${no}` : ''}</div>
        </div>`;
    } else if (q.option_counts && Object.keys(q.option_counts).length > 0) {
      body = bars(Object.entries(q.option_counts).sort((a, b) => b[1] - a[1]), q.response_count);
    } else if (q.text_answers && q.text_answers.length > 0) {
      const shown = q.text_answers.slice(0, 5);
      const rest = q.text_answers.slice(5);
      body = `
        <div class="an-texts">${shown.map(a => `<div class="an-text">${esc(a)}</div>`).join('')}</div>
        ${rest.length ? `<div class="an-texts an-more hidden">${rest.map(a => `<div class="an-text">${esc(a)}</div>`).join('')}</div>
        <button type="button" class="link-btn an-more-btn" style="margin-top:10px;">Show all ${q.text_answers.length}</button>` : ''}`;
    } else {
      body = `<p class="hint">No responses yet.</p>`;
    }
    return `
      <div class="an-card">
        <div class="an-card-head">
          <span class="an-q">${esc(q.question_text)}</span>
          <span class="an-type">${TYPE_LABELS[q.question_type] || ''} · ${q.response_count} answer${q.response_count === 1 ? '' : 's'}</span>
        </div>
        ${body}
      </div>`;
  }

  window.renderAnalyticsCards = function (data, emptyMessage) {
    if (!data.questions || data.questions.length === 0) {
      return `<div class="empty-state">${esc(emptyMessage || 'This form has no questions yet.')}</div>`;
    }
    return `<div class="an-grid">${data.questions.map(questionCard).join('')}</div>`;
  };

  window.bindAnalyticsCards = function (root) {
    root.querySelectorAll('.an-more-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const more = btn.previousElementSibling;
        const hidden = more.classList.toggle('hidden');
        btn.textContent = hidden ? `Show all` : 'Show fewer';
      });
    });
  };
})();
