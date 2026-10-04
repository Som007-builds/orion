"use client"

import * as React from "react"
import dynamic from "next/dynamic"
import "@scalar/api-reference-react/style.css"

const ApiReference = dynamic(
  () => import("@scalar/api-reference-react").then((mod) => mod.ApiReferenceReact),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-screen w-screen items-center justify-center font-sans text-sm text-neutral-500 bg-neutral-950 text-white">
        Loading API Documentation...
      </div>
    ),
  }
)

export default function DocsPage() {
  return (
    <div className="w-full min-h-screen">
      <ApiReference
        configuration={{
          spec: {
            url: "/openapi.json",
          },
        }}
      />
    </div>
  )
}
