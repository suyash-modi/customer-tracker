export type BackendMeta = {
  frame_w: number | null
  frame_h: number | null
  line_p1: [number, number] | null
  line_p2: [number, number] | null
  lines: Array<[[number, number], [number, number]]>
  zones: Array<{ name: string; points: Array<{ x: number; y: number }> }>
}

export type Session = {
  session_id: string
  entry_time: number | null
  exit_time: number | null
  events: string[]
  zone_visits?: Array<{ zone_name: string; entry_time: number; exit_time: number | null }>
}

export type Zone = {
  name: string
  points: Array<{ x: number; y: number }>
}

export async function postConfig(payload: { source: string }) {
  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    // Important: response bodies can only be read once. Read as text first,
    // then optionally parse as JSON.
    const raw = await res.text().catch(() => '')
    let errorMsg = 'Failed to start video source'
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        errorMsg = parsed?.detail || parsed?.message || raw
      } catch {
        errorMsg = raw
      }
    }
    throw new Error(errorMsg)
  }
}

export async function postLine(p1: [number, number], p2: [number, number]) {
  const res = await fetch('/api/line', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] }),
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to add line')
  }
}

export async function postLines(lines: Array<{ x1: number; y1: number; x2: number; y2: number }>) {
  const res = await fetch('/api/lines', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to set lines')
  }
}

export async function deleteLine(lineIndex: number) {
  const res = await fetch(`/api/lines/${lineIndex}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to delete line')
  }
}

export async function getMeta(): Promise<BackendMeta> {
  const res = await fetch('/api/meta')
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to load meta')
  }
  return (await res.json()) as BackendMeta
}

export async function getSessions(): Promise<Session[]> {
  const res = await fetch('/api/sessions')
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to load sessions')
  }
  const data = (await res.json()) as { sessions: Session[] }
  return data.sessions ?? []
}

export async function postZone(zone: Zone) {
  const res = await fetch('/api/zone', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(zone),
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to create zone')
  }
  return await res.json()
}

export async function getZones(): Promise<Zone[]> {
  const res = await fetch('/api/zones')
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to load zones')
  }
  const data = (await res.json()) as { zones: Zone[] }
  return data.zones ?? []
}

export async function deleteZone(zoneName: string) {
  const res = await fetch(`/api/zones/${encodeURIComponent(zoneName)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    throw new Error(raw || 'Failed to delete zone')
  }
  return await res.json()
}


