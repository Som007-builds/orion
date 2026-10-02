"use client"

import * as React from "react"

interface EntityContextType {
  activeEntityId: string
  setActiveEntityId: (id: string) => void
}

const EntityContext = React.createContext<EntityContextType>({
  activeEntityId: "",
  setActiveEntityId: () => {},
})

export function EntityProvider({ children }: { children: React.ReactNode }) {
  const [activeEntityId, setActiveEntityIdState] = React.useState<string>("")

  React.useEffect(() => {
    const saved = localStorage.getItem("orion_active_entity_id")
    if (saved) {
      setActiveEntityIdState(saved)
    }
  }, [])

  const setActiveEntityId = (id: string) => {
    setActiveEntityIdState(id)
    if (typeof window !== "undefined") {
      localStorage.setItem("orion_active_entity_id", id)
    }
  }

  return (
    <EntityContext.Provider value={{ activeEntityId, setActiveEntityId }}>
      {children}
    </EntityContext.Provider>
  )
}

export function useEntity() {
  return React.useContext(EntityContext)
}
