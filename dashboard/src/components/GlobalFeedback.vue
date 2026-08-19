<script setup lang="ts">
import { useMessage } from 'naive-ui'
import { storeToRefs } from 'pinia'
import { watch } from 'vue'

import { useUiStore } from '../stores/ui'

const message = useMessage()
const ui = useUiStore()
const { notice } = storeToRefs(ui)

watch(notice, (value) => {
  if (!value) return
  message[value.type](value.content)
  ui.clearNotice()
})
</script>

<template />
