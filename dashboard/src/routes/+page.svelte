<script lang="ts">
  import LoadingDots from '$lib/components/LoadingDots.svelte';
</script>

<main>
  <h1>OpenSwarm</h1>
  <LoadingDots label="Loading dashboard…" />
</main>

<style>
  main {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    font-family: monospace;
    color: #c9d1d9;
    background: #0d1117;
  }
  h1 {
    font-size: 1.4rem;
    font-weight: 600;
    margin-bottom: 1rem;
    letter-spacing: 0.05em;
  }
</style>
