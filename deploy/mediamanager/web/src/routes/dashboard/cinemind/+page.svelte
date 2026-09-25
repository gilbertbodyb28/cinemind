<script lang="ts">
	import { onMount } from 'svelte';
	import { Separator } from '$lib/components/ui/separator/index.js';
	import * as Sidebar from '$lib/components/ui/sidebar/index.js';
	import * as Breadcrumb from '$lib/components/ui/breadcrumb/index.js';
	import { resolve } from '$app/paths';

	// CineMind runs next to MediaManager (compose service `cinemind`, port 8001)
	// on the same host, so the tab follows whatever address MediaManager was opened on.
	let src = $state('');
	onMount(() => {
		src = `${window.location.protocol}//${window.location.hostname}:8001/dashboard`;
	});
</script>

<svelte:head>
	<title>CineMind - MediaManager</title>
</svelte:head>

<header class="flex h-16 shrink-0 items-center gap-2">
	<div class="flex items-center gap-2 px-4">
		<Sidebar.Trigger class="-ml-1" />
		<Separator class="mr-2 h-4" orientation="vertical" />
		<Breadcrumb.Root>
			<Breadcrumb.List>
				<Breadcrumb.Item class="hidden md:block">
					<Breadcrumb.Link href={resolve('/dashboard', {})}>MediaManager</Breadcrumb.Link>
				</Breadcrumb.Item>
				<Breadcrumb.Separator class="hidden md:block" />
				<Breadcrumb.Item>
					<Breadcrumb.Page>CineMind</Breadcrumb.Page>
				</Breadcrumb.Item>
			</Breadcrumb.List>
		</Breadcrumb.Root>
		{#if src}
			<a class="ml-4 text-sm underline opacity-70 hover:opacity-100" href={src} target="_blank" rel="noopener">
				Open in its own window
			</a>
		{/if}
	</div>
</header>

<main class="flex w-full flex-1 flex-col p-2 md:p-4">
	{#if src}
		<iframe
			title="CineMind"
			{src}
			class="h-[calc(100vh-6rem)] w-full flex-1 rounded-xl border-0"
			allow="autoplay; encrypted-media; fullscreen; picture-in-picture"
			allowfullscreen
		></iframe>
	{/if}
</main>
