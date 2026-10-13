<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
const props = defineProps({ open: Boolean, title: String })
const emit = defineEmits(['close'])
const element = ref(null)
watch(() => props.open, async (value) => { await nextTick(); if (!element.value) return; if (value && !element.value.open) element.value.showModal(); else if (!value && element.value.open) element.value.close() }, { immediate: true })
onUnmounted(() => element.value?.close())
</script>
<template><dialog ref="element" class="dialog" aria-label="详情" @cancel.prevent="emit('close')"><header class="dialog-head"><h2>{{ title }}</h2><button class="icon-button" aria-label="关闭" @click="emit('close')">×</button></header><div class="dialog-body"><slot /></div><footer v-if="$slots.actions" class="dialog-actions"><slot name="actions" /></footer></dialog></template>
