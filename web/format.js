const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});



export function money(cents) {
  return inr.format((cents || 0) / 100);
}

export function count(value) {
  const number = value || 0;
  // Intl compact notation in en-IN renders thousands as "T" (for hazar), which
  // reads as trillions to an English speaker. Use explicit Indian units.
  if (Math.abs(number) >= 1e7) return `${(number / 1e7).toFixed(1)}Cr`;
  if (Math.abs(number) >= 1e5) return `${(number / 1e5).toFixed(1)}L`;
  if (Math.abs(number) >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
  return String(number);
}

export function percent(value, digits = 2) {
  return `${((value || 0) * 100).toFixed(digits)}%`;
}

export function dateTime(iso) {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function shortDay(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
  });
}

export function todayISO() {
  // The services date their data in UTC (see `utcnow` in every model), so the
  // dashboard must ask for the same day. Using the browser's local date puts
  // the user a day ahead of the data for the first hours after local midnight.
  return new Date().toISOString().slice(0, 10);
}

export const KIND_LABELS = {
  TRACKING_DARK: 'Tracking dark',
  SPEND_WITHOUT_DELIVERY: 'Spend without delivery',
  TOTAL_CLICK_LOSS: 'Click loss',
  CONVERSION_COLLAPSE: 'Conversion collapse',
  CREATIVE_FATIGUE: 'Creative fatigue',
  NO_DATA: 'No data',
  IMPRESSIONS_SPIKE: 'Impressions spike',
  IMPRESSIONS_DROP: 'Impressions drop',
  CLICKS_SPIKE: 'Clicks spike',
  CLICKS_DROP: 'Clicks drop',
  CONVERSIONS_SPIKE: 'Conversions spike',
  CONVERSIONS_DROP: 'Conversions drop',
  SPEND_SPIKE: 'Spend spike',
  SPEND_DROP: 'Spend drop',
  CTR_SPIKE: 'CTR spike',
  CTR_DROP: 'CTR drop',
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind.replaceAll('_', ' ').toLowerCase();
}
