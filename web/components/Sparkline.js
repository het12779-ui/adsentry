export default {
  name: 'Sparkline',
  props: {
    values: { type: Array, default: () => [] },
    width: { type: Number, default: 118 },
    height: { type: Number, default: 26 },
  },
  computed: {
    points() {
      const values = this.values.length ? this.values : [0, 0];
      const max = Math.max(1, ...values);
      const stepX = this.width / Math.max(1, values.length - 1);
      const pad = 2;
      return values
        .map((value, index) => {
          const x = index * stepX;
          const y = this.height - pad - (value / max) * (this.height - pad * 2);
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ');
    },
    lastPoint() {
      const values = this.values.length ? this.values : [0];
      const max = Math.max(1, ...values);
      const index = values.length - 1;
      const stepX = this.width / Math.max(1, values.length - 1);
      return {
        x: index * stepX,
        y: this.height - 2 - (values[index] / max) * (this.height - 4),
      };
    },
    isFlatZero() {
      return this.values.every((value) => !value);
    },
  },
  template: `
    <svg
      class="spark"
      :width="width"
      :height="height"
      :viewBox="'0 0 ' + width + ' ' + height"
      role="img"
      :aria-label="'14 day impressions trend'"
    >
      <polyline :points="points" :class="{ 'spark-zero': isFlatZero }" />
      <circle :cx="lastPoint.x" :cy="lastPoint.y" r="2" />
    </svg>
  `,
};
