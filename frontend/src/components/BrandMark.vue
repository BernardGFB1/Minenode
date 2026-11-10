<template>
  <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="8" class="bg" />
    <path d="M8 11.5 16 7l8 4.5v9L16 25l-8-4.5z" class="stroke" />
    <path d="M8 11.5 16 16l8-4.5M16 16v9" class="stroke" />
  </svg>
</template>
<style scoped>
.mark { width: 34px; height: 34px; flex: none; }
.bg { fill: var(--accent-soft); }
.stroke { fill: none; stroke: var(--accent); stroke-width: 2.2; stroke-linejoin: round; }
</style>
