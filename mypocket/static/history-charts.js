/* Date-only labels/ranges come from the server. Never parse them as UTC Dates. */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('canvas[data-history-url]').forEach(async canvas => {
    const status = document.getElementById(`${canvas.id}-status`);
    try {
      const response = await fetch(canvas.dataset.historyUrl);
      if (!response.ok) throw new Error('Chart unavailable');
      const rows = await response.json();
      let chart;
      if (['flows', 'account-flows'].includes(canvas.dataset.historyKind)) {
        chart = charts.groupedBar(canvas, rows.map(r => r.label), [
          {label: canvas.dataset.historyKind === 'account-flows' ? 'Inflow' : 'Income', data: rows.map(r => r.inflow), color: '#34d399'},
          {label: canvas.dataset.historyKind === 'account-flows' ? 'Outflow' : 'Spending', data: rows.map(r => r.outflow), color: '#fb7185'},
        ]);
      } else {
        const field = canvas.dataset.historyKind === 'dividends' ? 'amount' : 'spending';
        chart = charts.bar(canvas, rows.map(r => r.label), rows.map(r => r[field]));
      }
      chart.options.scales.x.ticks = {maxRotation: 0, autoSkip: true, maxTicksLimit: 8};
      chart.options.plugins.tooltip.callbacks.title = items => rows[items[0].dataIndex].range;
      // Dense daily/weekly charts remain readable; tooltips + detail tables give values.
      chart.options.plugins.barValueLabels = rows.length <= 12;
      chart.update('none');
      canvas.dataset.bucketCount = String(rows.length);
      canvas.dataset.rangeStart = rows[0]?.start || '';
      canvas.dataset.rangeEnd = rows.at(-1)?.end || '';
      status.textContent = rows.some(r => (r.inflow || r.outflow || r.spending || r.amount))
        ? 'Exact dates shown in chart tooltips. Partial buckets are marked.'
        : 'No recorded activity in this range. Empty periods are shown as zero.';
    } catch (_) {
      status.textContent = 'Chart could not load. Reload to try again.';
    }
  });
});
