<script lang="ts">
  const { label = "Loading…" }: { label?: string } = $props();
</script>

<div class="loading-dots" aria-label={label} role="status">
  <span class="loading-dots__dot"></span>
  <span class="loading-dots__dot"></span>
  <span class="loading-dots__dot"></span>
</div>

<style>
  .loading-dots {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    padding: 4rem 0;
  }

  .loading-dots__dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--blue, #4a9eff);
    opacity: 0.5;
    animation: dot-pulse 1.2s ease-in-out infinite;
  }

  .loading-dots__dot:nth-child(2) { animation-delay: 0.2s; }
  .loading-dots__dot:nth-child(3) { animation-delay: 0.4s; }

  @keyframes dot-pulse {
    0%, 80%, 100% { opacity: 0.2; transform: scale(0.85); }
    40%           { opacity: 0.9; transform: scale(1.15); }
  }
</style>
