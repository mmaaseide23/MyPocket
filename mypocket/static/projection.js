/* Pure monthly investment arithmetic. Inputs are annual effective percentages. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.InvestmentProjection = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function inRange(value, min, max, label) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) {
      throw new RangeError(`${label} must be between ${min} and ${max}.`);
    }
  }
  function normalizeChanges(changes) {
    if (!Array.isArray(changes)) throw new RangeError('Use a list of contribution changes.');
    const sorted = changes.map(change => {
      if (!change || typeof change !== 'object') throw new RangeError('Each change needs a month and amount.');
      inRange(change.startMonth, 1, 720, 'Contribution start month');
      if (!Number.isInteger(change.startMonth)) throw new RangeError('Use a whole contribution start month.');
      inRange(change.monthly, 0, 1e9, 'Changed monthly contribution');
      return {startMonth: change.startMonth, monthly: change.monthly};
    }).sort((a,b) => a.startMonth - b.startMonth);
    if (sorted.some((change,index) => index > 0 && change.startMonth === sorted[index-1].startMonth)) {
      throw new RangeError('Use only one contribution change per start month.');
    }
    return sorted;
  }
  function project({initial, monthly, years, annualReturn, inflation, contributionChanges = []}) {
    inRange(initial, 0, 1e9, 'Starting amount');
    inRange(monthly, 0, 1e9, 'Monthly contribution');
    inRange(years, 0, 60, 'Years');
    if (!Number.isInteger(years)) throw new RangeError('Use a whole number of years.');
    inRange(annualReturn, -99, 100, 'Annual return');
    inRange(inflation, -20, 100, 'Inflation');
    const changes = normalizeChanges(contributionChanges);
    // log1p/expm1 preserve precision near zero. Dividing the annual rate by 12
    // would instead treat it as an APR and overstate an effective annual return.
    const monthlyRate = Math.expm1(Math.log1p(annualReturn / 100) / 12);
    let value = initial, deposits = 0, amount = monthly, next = 0;
    const points = [{year: 0, value, contributed: initial}];
    for (let month = 1; month <= years * 12; month++) {
      if (next < changes.length && changes[next].startMonth === month) amount = changes[next++].monthly;
      value = value * (1 + monthlyRate) + amount; // New amount applies at this month-end.
      deposits += amount;
      if (month % 12 === 0) points.push({year: month / 12, value, contributed: initial + deposits});
    }
    const contributed = initial + deposits;
    return {
      endValue: value, contributed, deposits, growth: value - contributed,
      appliedChanges: next, excludedChanges: changes.length - next,
      realValue: value / Math.exp(years * Math.log1p(inflation / 100)), points,
    };
  }
  return {project, normalizeChanges};
});
