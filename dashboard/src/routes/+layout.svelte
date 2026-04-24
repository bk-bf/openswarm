<script lang="ts">
  let { children } = $props();
</script>

<svelte:head>
  <title>OpenSwarm</title>
</svelte:head>

{@render children()}
