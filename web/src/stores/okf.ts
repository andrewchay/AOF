/*
 * Copyright (C) 2026 Andrewchay
 * Use of this software is governed by the Business Source License
 * included in the LICENSE file of this repository.
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0.
 */
import { defineStore } from 'pinia'
import * as okfApi from '@/api/okf'

export const useOkfStore = defineStore('okf', {
  state: () => ({
    bundles: [] as okfApi.OkfBundle[],
    activeBundle: '' as string,
    activeBundleTitle: '' as string,
    indexMd: '' as string,
    loadingBundles: false,
    loadingIndex: false,
    selectedConcept: '' as string,
    conceptRaw: '' as string,
    lintResult: null as null | { error_count: number; warning_count: number; issues: object[] },
  }),
  actions: {
    async loadBundles() {
      this.loadingBundles = true
      try {
        const res = await okfApi.listBundles()
        this.bundles = res.bundles
        if (!this.activeBundle && res.bundles.length > 0) {
          this.activeBundle = res.bundles[0].name
          this.activeBundleTitle = res.bundles[0].title
        }
      } finally {
        this.loadingBundles = false
      }
    },
    selectBundle(name: string, title: string) {
      this.activeBundle = name
      this.activeBundleTitle = title
      this.indexMd = ''
      this.selectedConcept = ''
      this.conceptRaw = ''
    },
    async loadIndex() {
      if (!this.activeBundle) return
      this.loadingIndex = true
      try {
        const res = await okfApi.getBundleIndex(this.activeBundle)
        this.indexMd = res.index_md ?? ''
      } finally {
        this.loadingIndex = false
      }
    },
    async openConcept(path: string) {
      if (!this.activeBundle) return
      this.selectedConcept = path
      const res = await okfApi.getConcept(this.activeBundle, path)
      this.conceptRaw = res.concept ?? res.error ?? ''
    },
    async runLint() {
      if (!this.activeBundle) return
      this.lintResult = await okfApi.lintBundle(this.activeBundle)
    },
  },
})
