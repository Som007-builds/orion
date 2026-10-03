"use client"

import * as React from "react"
import dynamic from "next/dynamic"
import "@scalar/api-reference-react/style.css"
import { Badge } from "@/components/ui/badge"
import { BookOpen, ExternalLink } from "lucide-react"

// Dynamically import ApiReferenceReact for client-only rendering without Turbopack ESM refract bugs
const ApiReference = dynamic(
  () => import("@scalar/api-reference-react").then((mod) => mod.ApiReferenceReact),
  {
    ssr: false,
    loading: () => (
      <div className="p-12 text-center text-xs font-sans text-muted-foreground animate-pulse">
        Loading OpenAPI 3.1 contract registry viewer...
      </div>
    ),
  }
)

export function SwaggerViewer() {
  const [specUrl, setSpecUrl] = React.useState("/openapi.json")

  return (
    <div className="space-y-4 font-sans">
      {/* Spec Source Switcher & Metadata Toolbar */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-3 bg-card border border-border rounded-lg text-xs">
        <div className="flex items-center gap-2">
          <BookOpen className="h-4 w-4 text-primary" />
          <span className="font-semibold text-foreground">OpenAPI 3.1 Contract Specification</span>
          <Badge variant="outline" className="text-[10px]">
            45 Operations · 42 Paths
          </Badge>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Source:</span>
          <div className="flex items-center gap-1 bg-muted p-0.5 rounded-md">
            <button
              onClick={() => setSpecUrl("/openapi.json")}
              className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                specUrl === "/openapi.json"
                  ? "bg-card text-foreground font-semibold shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Frozen v1 Enclave Spec
            </button>
            <button
              onClick={() => setSpecUrl("http://localhost:8000/openapi.json")}
              className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                specUrl === "http://localhost:8000/openapi.json"
                  ? "bg-card text-foreground font-semibold shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Live FastAPI Backend
            </button>
          </div>
          <a
            href={specUrl}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 px-2.5 py-1 text-xs text-primary hover:underline"
          >
            Raw JSON <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>

      {/* Embedded API Reference Container */}
      <div className="scalar-container bg-card rounded-lg border border-border overflow-hidden min-h-[600px]">
        <ApiReference
          configuration={{
            spec: {
              url: specUrl,
            },
            theme: "alternate",
            hideDarkModeToggle: true,
          }}
        />
      </div>
    </div>
  )
}
