import './App.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getMeta, getSessions, postConfig, postLine, deleteLine, postZone, getZones, deleteZone, type Session, type Zone } from './api'

type CreationMode = 'none' | 'line' | 'zone'

function App() {
  const [sourceType, setSourceType] = useState<'camera' | 'youtube'>('camera')
  const [source, setSource] = useState<string>('0')
  const [started, setStarted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [meta, setMeta] = useState<{ frame_w: number | null; frame_h: number | null; lines: Array<[[number, number], [number, number]]>; zones: Zone[] }>({
    frame_w: null,
    frame_h: null,
    lines: [],
    zones: [],
  })
  const [creationMode, setCreationMode] = useState<CreationMode>('none')
  const [currentLinePts, setCurrentLinePts] = useState<Array<[number, number]>>([]) // Points for the line being drawn
  const [currentZonePts, setCurrentZonePts] = useState<Array<[number, number]>>([]) // Points for the zone being drawn (4 points)
  const [zoneNameInput, setZoneNameInput] = useState<string>('')
  const [showZoneNameDialog, setShowZoneNameDialog] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])

  const imgRef = useRef<HTMLImageElement | null>(null)
  const overlayRef = useRef<HTMLCanvasElement | null>(null)

  const streamUrl = useMemo(() => '/api/stream', [])

  const activeCount = useMemo(() => sessions.filter((s) => !s.exit_time).length, [sessions])

  function formatTime(sec: number | null): string {
    if (sec == null) return '—'
    const d = new Date(sec * 1000)
    return d.toLocaleTimeString()
  }

  function applySourceType(val: 'camera' | 'youtube') {
    setSourceType(val)
    if (val === 'camera') {
      setSource('0')
    } else {
      setSource('https://www.youtube.com/watch?v=')
    }
  }

  async function onStart() {
    if (!source.trim()) {
      setError('Please enter a source (0, file path, or YouTube URL).')
      return
    }
    setError(null)
    try {
      await postConfig({ source: source.trim() })
      setStarted(true)
    } catch (e: any) {
      setError(e?.message ?? String(e))
    }
  }

  // Poll backend for metadata and sessions while running.
  useEffect(() => {
    if (!started) return
    let cancelled = false
    const tick = async () => {
      try {
        const m = await getMeta()
        if (!cancelled) {
          setMeta({ 
            frame_w: m.frame_w, 
            frame_h: m.frame_h, 
            lines: Array.isArray(m.lines) ? m.lines : (m.lines || []),
            zones: Array.isArray(m.zones) ? m.zones : (m.zones || [])
          })
        }
        const ss = await getSessions()
        if (!cancelled) setSessions(ss)
      } catch (e) {
        // ignore transient errors
      }
    }
    tick() // Initial call
    const id = window.setInterval(tick, 5000) // Update every 5 seconds
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [started])

  const drawOverlay = useCallback(() => {
    const canvas = overlayRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    
    const img = imgRef.current
    if (!img || !meta.frame_w || !meta.frame_h) return
    
    const sx = img.clientWidth / meta.frame_w
    const sy = img.clientHeight / meta.frame_h
    
    // Draw all saved zones from backend
    const zoneColors = ['rgba(255, 100, 100, 0.3)', 'rgba(100, 255, 100, 0.3)', 'rgba(100, 100, 255, 0.3)', 'rgba(255, 255, 100, 0.3)', 'rgba(255, 100, 255, 0.3)']
    const zoneBorderColors = ['#FF6464', '#64FF64', '#6464FF', '#FFFF64', '#FF64FF']
    if (meta.zones.length > 0) {
      meta.zones.forEach((zone, idx) => {
        const color = zoneColors[idx % zoneColors.length]
        const borderColor = zoneBorderColors[idx % zoneBorderColors.length]
        
        if (zone.points.length === 4) {
          ctx.fillStyle = color
          ctx.strokeStyle = borderColor
          ctx.lineWidth = 2
          
          ctx.beginPath()
          const scaledPoints = zone.points.map(p => [p.x * sx, p.y * sy])
          ctx.moveTo(scaledPoints[0][0], scaledPoints[0][1])
          for (let i = 1; i < scaledPoints.length; i++) {
            ctx.lineTo(scaledPoints[i][0], scaledPoints[i][1])
          }
          ctx.closePath()
          ctx.fill()
          ctx.stroke()
          
          // Draw zone name
          const centerX = scaledPoints.reduce((sum, p) => sum + p[0], 0) / scaledPoints.length
          const centerY = scaledPoints.reduce((sum, p) => sum + p[1], 0) / scaledPoints.length
          ctx.fillStyle = borderColor
          ctx.font = 'bold 14px sans-serif'
          ctx.textAlign = 'center'
          ctx.fillText(zone.name, centerX, centerY)
          ctx.textAlign = 'left'
        }
      })
    }
    
    // Draw current zone being drawn
    if (currentZonePts.length > 0) {
      ctx.fillStyle = 'rgba(255, 200, 100, 0.3)'
      ctx.strokeStyle = '#FFC864'
      ctx.lineWidth = 2
      
      for (const [x, y] of currentZonePts) {
        ctx.beginPath()
        ctx.arc(x, y, 6, 0, Math.PI * 2)
        ctx.fillStyle = '#FFC864'
        ctx.fill()
        ctx.fillStyle = 'rgba(255, 200, 100, 0.3)'
      }
      
      if (currentZonePts.length >= 2) {
        ctx.beginPath()
        ctx.moveTo(currentZonePts[0][0], currentZonePts[0][1])
        for (let i = 1; i < currentZonePts.length; i++) {
          ctx.lineTo(currentZonePts[i][0], currentZonePts[i][1])
        }
        if (currentZonePts.length === 4) {
          ctx.closePath()
        }
        ctx.stroke()
        if (currentZonePts.length === 4) {
          ctx.fill()
        }
      }
    }
    
    // Draw all saved lines from backend
    const colors = ['#FBBF24', '#EC4899', '#8B5CF6', '#10B981', '#EF4444'] // Yellow, Pink, Purple, Green, Red
    if (meta.lines.length > 0) {
      meta.lines.forEach((line, idx) => {
        const [p1, p2] = line
        const color = colors[idx % colors.length]
        ctx.strokeStyle = color
        ctx.fillStyle = color
        ctx.lineWidth = 3
        
        const x1 = p1[0] * sx
        const y1 = p1[1] * sy
        const x2 = p2[0] * sx
        const y2 = p2[1] * sy
        
        ctx.beginPath()
        ctx.moveTo(x1, y1)
        ctx.lineTo(x2, y2)
        ctx.stroke()
        
        // Draw endpoints
        ctx.beginPath()
        ctx.arc(x1, y1, 6, 0, Math.PI * 2)
        ctx.fill()
        ctx.beginPath()
        ctx.arc(x2, y2, 6, 0, Math.PI * 2)
        ctx.fill()
        
        // Label
        ctx.font = '14px sans-serif'
        ctx.fillText(`Line ${idx + 1}`, x1 + 10, y1 - 10)
      })
    }
    
    // Draw current line being drawn
    if (currentLinePts.length > 0) {
      ctx.fillStyle = '#FBBF24'
      ctx.strokeStyle = '#FBBF24'
      ctx.lineWidth = 3
      for (const [x, y] of currentLinePts) {
        ctx.beginPath()
        ctx.arc(x, y, 6, 0, Math.PI * 2)
        ctx.fill()
      }
      if (currentLinePts.length === 2) {
        ctx.beginPath()
        ctx.moveTo(currentLinePts[0][0], currentLinePts[0][1])
        ctx.lineTo(currentLinePts[1][0], currentLinePts[1][1])
        ctx.stroke()
      }
    }
  }, [meta.frame_w, meta.frame_h, meta.lines, meta.zones, currentLinePts, currentZonePts])

  // Keep overlay canvas in sync with image size and redraw on resize in a lightweight way.
  useEffect(() => {
    const img = imgRef.current
    const canvas = overlayRef.current
    if (!img || !canvas) return

    const handleResize = () => {
      // Use requestAnimationFrame to avoid layout thrash and make UI feel smoother
      window.requestAnimationFrame(() => {
        if (!imgRef.current || !overlayRef.current) return
        const c = overlayRef.current
        c.width = imgRef.current.clientWidth
        c.height = imgRef.current.clientHeight
        drawOverlay()
      })
    }

    img.addEventListener('load', handleResize)
    window.addEventListener('resize', handleResize)
    handleResize()

    return () => {
      img.removeEventListener('load', handleResize)
      window.removeEventListener('resize', handleResize)
    }
  }, [drawOverlay, started])

  // Redraw overlay when meta/points change without touching layout.
  useEffect(() => {
    if (!started) return
    window.requestAnimationFrame(() => {
      drawOverlay()
    })
  }, [started, drawOverlay])

  function toFrameCoords(xImg: number, yImg: number): [number, number] {
    const fw = meta.frame_w
    const fh = meta.frame_h
    const img = imgRef.current
    if (!fw || !fh || !img) return [xImg, yImg]
    const sx = fw / img.clientWidth
    const sy = fh / img.clientHeight
    return [Math.round(xImg * sx), Math.round(yImg * sy)]
  }

  async function onOverlayClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!started) return
    if (!meta.frame_w || !meta.frame_h) {
      setError('Waiting for first frame from backend before setting line/zone...')
      return
    }
    
    if (creationMode === 'none') return
    
    const canvas = overlayRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = Math.round(e.clientX - rect.left)
    const y = Math.round(e.clientY - rect.top)

    if (creationMode === 'line') {
      if (currentLinePts.length >= 2) {
        // Start a new line
        setCurrentLinePts([])
        return
      }
      const next = [...currentLinePts, [x, y] as [number, number]]
      setCurrentLinePts(next)
      if (next.length === 2) {
        const p1 = toFrameCoords(next[0][0], next[0][1])
        const p2 = toFrameCoords(next[1][0], next[1][1])
        try {
          await postLine(p1, p2)
          setCurrentLinePts([]) // Reset after adding
        } catch (e: any) {
          setError(e?.message ?? String(e))
          setCurrentLinePts([])
        }
      }
    } else if (creationMode === 'zone') {
      if (currentZonePts.length >= 4) {
        // Start a new zone
        setCurrentZonePts([])
        return
      }
      const next = [...currentZonePts, [x, y] as [number, number]]
      setCurrentZonePts(next)
      if (next.length === 4) {
        // Show dialog to enter zone name
        setShowZoneNameDialog(true)
      }
    }
  }
  
  async function createZone() {
    if (!zoneNameInput.trim() || currentZonePts.length !== 4) return
    
    try {
      const zonePoints = currentZonePts.map(pt => {
        const [fx, fy] = toFrameCoords(pt[0], pt[1])
        return { x: fx, y: fy }
      })
      await postZone({ name: zoneNameInput.trim(), points: zonePoints })
      setCurrentZonePts([])
      setZoneNameInput('')
      setShowZoneNameDialog(false)
      setCreationMode('none')
    } catch (e: any) {
      setError(e?.message ?? String(e))
    }
  }

  async function deleteLineHandler(lineIndex: number) {
    try {
      await deleteLine(lineIndex)
    } catch (e: any) {
      setError(e?.message ?? String(e))
    }
  }

  function resetCurrentLine() {
    setCurrentLinePts([])
    drawOverlay()
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #0a0e27 0%, #1a1f3a 50%, #0f172a 100%)',
        padding: '24px 16px',
      }}
    >
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>
        {/* Header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 24,
            paddingBottom: 20,
            borderBottom: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 28,
                fontWeight: 700,
                background: 'linear-gradient(135deg, #ffffff 0%, #a0aec0 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                backgroundClip: 'text',
                letterSpacing: '-0.02em',
              }}
            >
              Customer Journey Stitching
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, opacity: 0.6, color: '#cbd5e1' }}>
              Real-time person tracking & session analytics
            </p>
          </div>
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'center',
            }}
          >
            <span
              style={{
                padding: '6px 14px',
                borderRadius: 8,
                border: '1px solid rgba(34, 197, 94, 0.2)',
                background: 'linear-gradient(135deg, rgba(16, 163, 74, 0.15) 0%, rgba(34, 197, 94, 0.1) 100%)',
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
                color: '#86efac',
                fontWeight: 600,
                boxShadow: '0 2px 8px rgba(34, 197, 94, 0.1)',
              }}
            >
              OpenVINO + ReID
            </span>
          </div>
        </div>

        {/* Main Content Grid */}
        <div
          className="main-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) 380px',
            gap: 24,
            alignItems: 'start',
          }}
        >
          {/* Video Panel */}
          <div>
            <div
              style={{
                background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(2, 6, 23, 0.98) 100%)',
                borderRadius: 16,
                border: '1px solid rgba(255, 255, 255, 0.1)',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)',
                padding: 20,
                backdropFilter: 'blur(10px)',
              }}
            >
              {/* Controls */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
                <select
                  value={sourceType}
                  onChange={(e) => applySourceType(e.target.value as 'camera' | 'youtube')}
                  style={{
                    padding: '10px 14px',
                    borderRadius: 10,
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    background: 'rgba(15, 23, 42, 0.8)',
                    color: '#e2e8f0',
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    outline: 'none',
                  }}
                  onFocus={(e) => (e.target.style.borderColor = 'rgba(59, 130, 246, 0.5)')}
                  onBlur={(e) => (e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)')}
                >
                  <option value="camera">📹 Live Camera</option>
                  <option value="youtube">▶️ YouTube Video</option>
                </select>
                <input
                  id="source"
                  type="text"
                  value={source}
                  placeholder={
                    sourceType === 'camera'
                      ? 'Webcam index (0, 1, ...)'
                      : 'YouTube URL or local file path'
                  }
                  onChange={(e) => setSource(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !started) onStart()
                  }}
                  style={{
                    flex: 1,
                    minWidth: 200,
                    padding: '10px 16px',
                    borderRadius: 10,
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    background: 'rgba(15, 23, 42, 0.8)',
                    color: '#e2e8f0',
                    fontSize: 13,
                    outline: 'none',
                    transition: 'all 0.2s',
                  }}
                  onFocus={(e) => {
                    e.target.style.borderColor = 'rgba(59, 130, 246, 0.5)'
                    e.target.style.background = 'rgba(15, 23, 42, 0.95)'
                  }}
                  onBlur={(e) => {
                    e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)'
                    e.target.style.background = 'rgba(15, 23, 42, 0.8)'
                  }}
                />
                <button
                  onClick={onStart}
                  disabled={started}
                  style={{
                    padding: '10px 20px',
                    borderRadius: 10,
                    border: 'none',
                    background: started
                      ? 'rgba(34, 197, 94, 0.2)'
                      : 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)',
                    color: '#ffffff',
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: started ? 'not-allowed' : 'pointer',
                    transition: 'all 0.2s',
                    boxShadow: started ? 'none' : '0 4px 12px rgba(59, 130, 246, 0.3)',
                    opacity: started ? 0.6 : 1,
                  }}
                  onMouseEnter={(e) => {
                    if (!started) {
                      e.currentTarget.style.transform = 'translateY(-1px)'
                      e.currentTarget.style.boxShadow = '0 6px 16px rgba(59, 130, 246, 0.4)'
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!started) {
                      e.currentTarget.style.transform = 'translateY(0)'
                      e.currentTarget.style.boxShadow = '0 4px 12px rgba(59, 130, 246, 0.3)'
                    }
                  }}
                >
                  {started ? '✓ Running' : '▶ Start'}
                </button>
                <button
                  onClick={() => {
                    setCreationMode(creationMode === 'line' ? 'none' : 'line')
                    setCurrentLinePts([])
                    setCurrentZonePts([])
                  }}
                  disabled={!started}
                  title="Add Entry/Exit Line"
                  style={{
                    padding: '10px 16px',
                    borderRadius: 10,
                    border: `1px solid ${creationMode === 'line' ? 'rgba(251, 191, 36, 0.5)' : 'rgba(255, 255, 255, 0.15)'}`,
                    background: creationMode === 'line' ? 'rgba(251, 191, 36, 0.2)' : 'rgba(15, 23, 42, 0.8)',
                    color: creationMode === 'line' ? '#FBBF24' : '#e2e8f0',
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: !started ? 'not-allowed' : 'pointer',
                    transition: 'all 0.2s',
                    opacity: !started ? 0.5 : 1,
                  }}
                >
                  {creationMode === 'line' ? '✓ Line Mode' : '➕ Add Line'}
                </button>
                <button
                  onClick={() => {
                    setCreationMode(creationMode === 'zone' ? 'none' : 'zone')
                    setCurrentLinePts([])
                    setCurrentZonePts([])
                  }}
                  disabled={!started}
                  title="Add Zone (4 points)"
                  style={{
                    padding: '10px 16px',
                    borderRadius: 10,
                    border: `1px solid ${creationMode === 'zone' ? 'rgba(100, 200, 255, 0.5)' : 'rgba(255, 255, 255, 0.15)'}`,
                    background: creationMode === 'zone' ? 'rgba(100, 200, 255, 0.2)' : 'rgba(15, 23, 42, 0.8)',
                    color: creationMode === 'zone' ? '#64C8FF' : '#e2e8f0',
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: !started ? 'not-allowed' : 'pointer',
                    transition: 'all 0.2s',
                    opacity: !started ? 0.5 : 1,
                  }}
                >
                  {creationMode === 'zone' ? '✓ Zone Mode' : '📍 Add Zone'}
                </button>
                {(currentLinePts.length > 0 || currentZonePts.length > 0) && (
                  <button
                    onClick={() => {
                      setCurrentLinePts([])
                      setCurrentZonePts([])
                      setCreationMode('none')
                    }}
                    title="Cancel current drawing"
                    style={{
                      padding: '10px 16px',
                      borderRadius: 10,
                      border: '1px solid rgba(255, 255, 255, 0.15)',
                      background: 'rgba(15, 23, 42, 0.8)',
                      color: '#e2e8f0',
                      fontSize: 13,
                      fontWeight: 500,
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                    }}
                  >
                    ↻ Cancel
                  </button>
                )}
              </div>

              {/* Error Message */}
              {error && (
                <div
                  style={{
                    background: 'linear-gradient(135deg, rgba(127, 29, 29, 0.3) 0%, rgba(185, 28, 28, 0.2) 100%)',
                    color: '#fecaca',
                    padding: '12px 16px',
                    borderRadius: 10,
                    marginBottom: 16,
                    fontSize: 13,
                    border: '1px solid rgba(220, 38, 38, 0.3)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                  }}
                >
                  <span style={{ fontSize: 16 }}>⚠️</span>
                  <span>{error}</span>
                </div>
              )}

              {/* Video Container */}
              <div
                style={{
                  position: 'relative',
                  width: '100%',
                  borderRadius: 12,
                  overflow: 'hidden',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  background: 'radial-gradient(circle at center, rgba(15, 23, 42, 0.8) 0%, rgba(2, 6, 23, 0.95) 100%)',
                  minHeight: 400,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {/* Status Badges */}
                <div
                  style={{
                    position: 'absolute',
                    top: 12,
                    left: 12,
                    zIndex: 3,
                    display: 'flex',
                    gap: 8,
                    alignItems: 'center',
                    flexWrap: 'wrap',
                  }}
                >
                  <div
                    style={{
                      padding: '6px 12px',
                      borderRadius: 8,
                      background: 'rgba(15, 23, 42, 0.9)',
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      backdropFilter: 'blur(8px)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 12,
                      fontWeight: 500,
                    }}
                  >
                    <span
                      style={{
                        display: 'inline-block',
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: started ? '#22c55e' : '#6b7280',
                        boxShadow: started ? '0 0 8px rgba(34, 197, 94, 0.6)' : 'none',
                        animation: started ? 'pulse 2s infinite' : 'none',
                      }}
                    />
                    <span style={{ color: started ? '#86efac' : '#9ca3af' }}>
                      {started ? 'Streaming' : 'Idle'}
                    </span>
                  </div>
                      {started && (
                    <div
                      style={{
                        padding: '6px 12px',
                        borderRadius: 8,
                        background: 'rgba(15, 23, 42, 0.9)',
                        border: '1px solid rgba(255, 255, 255, 0.1)',
                        backdropFilter: 'blur(8px)',
                        fontSize: 12,
                        color: meta.lines.length > 0 ? '#86efac' : '#fbbf24',
                        fontWeight: 500,
                      }}
                    >
                      {meta.lines.length > 0 ? `📊 Analytics Mode (${meta.lines.length} line${meta.lines.length > 1 ? 's' : ''})` : '📹 Raw Feed'}
                    </div>
                  )}
                </div>

                {/* Video Content */}
                {!started ? (
                  <div
                    style={{
                      padding: '60px 40px',
                      textAlign: 'center',
                      color: 'rgba(226, 232, 240, 0.7)',
                    }}
                  >
                    <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.5 }}>📹</div>
                    <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 8, color: '#e2e8f0' }}>
                      Ready to Start
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                      Enter a source and click <strong style={{ color: '#60a5fa' }}>Start</strong>. When video
                      appears, click exactly <strong style={{ color: '#fbbf24' }}>2 points</strong> on the feed to
                      add an Entry/Exit line. You can add multiple lines.
                    </div>
                  </div>
                ) : (
                  <>
                    <img
                      ref={imgRef}
                      src={streamUrl}
                      alt="stream"
                      style={{
                        width: '100%',
                        height: 'auto',
                        display: 'block',
                        maxHeight: '70vh',
                        objectFit: 'contain',
                      }}
                    />
                    <canvas
                      ref={overlayRef}
                      onClick={onOverlayClick}
                      style={{
                        position: 'absolute',
                        left: 0,
                        top: 0,
                        width: '100%',
                        height: '100%',
                        cursor: (creationMode === 'line' && currentLinePts.length < 2) || (creationMode === 'zone' && currentZonePts.length < 4) ? 'crosshair' : 'default',
                        pointerEvents: (creationMode === 'line' && currentLinePts.length < 2) || (creationMode === 'zone' && currentZonePts.length < 4) ? 'auto' : 'none',
                      }}
                    />
                  </>
                )}
              </div>

              {/* Zone Name Dialog */}
              {showZoneNameDialog && (
                <div
                  style={{
                    position: 'fixed',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    background: 'rgba(0, 0, 0, 0.7)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    zIndex: 1000,
                  }}
                  onClick={() => {
                    setShowZoneNameDialog(false)
                    setZoneNameInput('')
                    setCurrentZonePts([])
                  }}
                >
                  <div
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(2, 6, 23, 0.98) 100%)',
                      borderRadius: 16,
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      padding: 24,
                      minWidth: 300,
                    }}
                  >
                    <div style={{ marginBottom: 16, fontSize: 18, fontWeight: 600, color: '#e2e8f0' }}>
                      Name Your Zone
                    </div>
                    <input
                      type="text"
                      value={zoneNameInput}
                      onChange={(e) => setZoneNameInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          createZone()
                        } else if (e.key === 'Escape') {
                          setShowZoneNameDialog(false)
                          setZoneNameInput('')
                          setCurrentZonePts([])
                        }
                      }}
                      placeholder="e.g., Checkout Area, Entrance, Aisle 1"
                      autoFocus
                      style={{
                        width: '100%',
                        padding: '12px 16px',
                        borderRadius: 10,
                        border: '1px solid rgba(255, 255, 255, 0.15)',
                        background: 'rgba(15, 23, 42, 0.8)',
                        color: '#e2e8f0',
                        fontSize: 14,
                        marginBottom: 16,
                        outline: 'none',
                      }}
                    />
                    <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                      <button
                        onClick={() => {
                          setShowZoneNameDialog(false)
                          setZoneNameInput('')
                          setCurrentZonePts([])
                        }}
                        style={{
                          padding: '10px 20px',
                          borderRadius: 10,
                          border: '1px solid rgba(255, 255, 255, 0.15)',
                          background: 'rgba(15, 23, 42, 0.8)',
                          color: '#e2e8f0',
                          fontSize: 13,
                          fontWeight: 500,
                          cursor: 'pointer',
                        }}
                      >
                        Cancel
                      </button>
                      <button
                        onClick={createZone}
                        disabled={!zoneNameInput.trim()}
                        style={{
                          padding: '10px 20px',
                          borderRadius: 10,
                          border: '1px solid rgba(100, 200, 255, 0.5)',
                          background: 'rgba(100, 200, 255, 0.2)',
                          color: '#64C8FF',
                          fontSize: 13,
                          fontWeight: 500,
                          cursor: zoneNameInput.trim() ? 'pointer' : 'not-allowed',
                          opacity: zoneNameInput.trim() ? 1 : 0.5,
                        }}
                      >
                        Create Zone
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Zones Management */}
              {started && meta.zones.length > 0 && (
                <div
                  style={{
                    marginTop: 16,
                    padding: '16px',
                    borderRadius: 10,
                    background: 'rgba(15, 23, 42, 0.6)',
                    border: '1px solid rgba(255, 255, 255, 0.1)',
                  }}
                >
                  <div style={{ marginBottom: 12, fontSize: 14, fontWeight: 600, color: '#e2e8f0' }}>
                    Zones ({meta.zones.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {meta.zones.map((zone, idx) => {
                      const zoneColors = ['#FF6464', '#64FF64', '#6464FF', '#FFFF64', '#FF64FF']
                      const color = zoneColors[idx % zoneColors.length]
                      return (
                        <div
                          key={zone.name}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '10px 12px',
                            borderRadius: 8,
                            background: 'rgba(15, 23, 42, 0.8)',
                            border: `1px solid ${color}40`,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <div
                              style={{
                                width: 16,
                                height: 16,
                                borderRadius: 4,
                                background: `${color}40`,
                                border: `2px solid ${color}`,
                              }}
                            />
                            <div>
                              <div style={{ fontSize: 13, fontWeight: 500, color: '#e2e8f0' }}>
                                {zone.name}
                              </div>
                              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
                                {zone.points.length} points
                              </div>
                            </div>
                          </div>
                          <button
                            onClick={async () => {
                              try {
                                await deleteZone(zone.name)
                              } catch (e: any) {
                                setError(e?.message ?? String(e))
                              }
                            }}
                            style={{
                              padding: '6px 12px',
                              borderRadius: 6,
                              border: '1px solid rgba(239, 68, 68, 0.3)',
                              background: 'rgba(239, 68, 68, 0.1)',
                              color: '#fca5a5',
                              fontSize: 12,
                              fontWeight: 500,
                              cursor: 'pointer',
                              transition: 'all 0.2s',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)'
                              e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.5)'
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'
                              e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.3)'
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Lines Management */}
              {started && meta.lines.length > 0 && (
                <div
                  style={{
                    marginTop: 16,
                    padding: '16px',
                    borderRadius: 10,
                    background: 'rgba(15, 23, 42, 0.6)',
                    border: '1px solid rgba(255, 255, 255, 0.1)',
                  }}
                >
                  <div style={{ marginBottom: 12, fontSize: 14, fontWeight: 600, color: '#e2e8f0' }}>
                    Entry/Exit Lines ({meta.lines.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {meta.lines.map((line, idx) => {
                      const colors = ['#FBBF24', '#EC4899', '#8B5CF6', '#10B981', '#EF4444']
                      const color = colors[idx % colors.length]
                      return (
                        <div
                          key={idx}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '10px 12px',
                            borderRadius: 8,
                            background: 'rgba(15, 23, 42, 0.8)',
                            border: `1px solid ${color}40`,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <div
                              style={{
                                width: 4,
                                height: 24,
                                borderRadius: 2,
                                background: color,
                              }}
                            />
                            <div>
                              <div style={{ fontSize: 13, fontWeight: 500, color: '#e2e8f0' }}>
                                Line {idx + 1}
                              </div>
                              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
                                {line && line[0] && line[1] ? (
                                  <>({line[0][0]}, {line[0][1]}) → ({line[1][0]}, {line[1][1]})</>
                                ) : (
                                  <>Invalid line data</>
                                )}
                              </div>
                            </div>
                          </div>
                          <button
                            onClick={() => deleteLineHandler(idx)}
                            style={{
                              padding: '6px 12px',
                              borderRadius: 6,
                              border: '1px solid rgba(239, 68, 68, 0.3)',
                              background: 'rgba(239, 68, 68, 0.1)',
                              color: '#fca5a5',
                              fontSize: 12,
                              fontWeight: 500,
                              cursor: 'pointer',
                              transition: 'all 0.2s',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)'
                              e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.5)'
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'
                              e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.3)'
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      )
                    })}
                  </div>
                  <div
                    style={{
                      marginTop: 12,
                      padding: '8px 12px',
                      borderRadius: 6,
                      background: 'rgba(59, 130, 246, 0.1)',
                      border: '1px solid rgba(59, 130, 246, 0.2)',
                      fontSize: 12,
                      color: '#93c5fd',
                      lineHeight: 1.5,
                    }}
                  >
                    💡 Click 2 points on the video to add another line. Each line independently detects entry/exit events.
                  </div>
                </div>
              )}

              {/* Footer Info */}
              <div
                style={{
                  marginTop: 16,
                  padding: '12px 16px',
                  borderRadius: 10,
                  background: 'rgba(15, 23, 42, 0.5)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 16,
                  flexWrap: 'wrap',
                  fontSize: 12,
                  color: 'rgba(226, 232, 240, 0.8)',
                }}
              >
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <div>
                    <span style={{ opacity: 0.7 }}>ReID threshold:</span>{' '}
                    <strong style={{ color: '#86efac' }}>0.62</strong>
                  </div>
                  <div
                    style={{
                      padding: '3px 8px',
                      borderRadius: 6,
                      fontSize: 11,
                      background: 'rgba(15, 23, 42, 0.8)',
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      color: '#a0aec0',
                    }}
                  >
                    One session / person / video
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <div>
                    <span style={{ opacity: 0.7 }}>Frame:</span>{' '}
                    <strong>
                      {meta.frame_w ?? '?'} × {meta.frame_h ?? '?'}
                    </strong>
                  </div>
                  <div>
                    <span style={{ opacity: 0.7 }}>Lines:</span>{' '}
                    <strong style={{ color: meta.lines.length > 0 ? '#86efac' : '#fbbf24' }}>
                      {meta.lines.length} active
                    </strong>
                    {currentLinePts.length > 0 && (
                      <span style={{ marginLeft: 8, color: '#fbbf24', fontSize: 11 }}>
                        (drawing: {currentLinePts.length}/2)
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Sessions Panel */}
          <div>
            <div
              style={{
                background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(2, 6, 23, 0.98) 100%)',
                borderRadius: 16,
                border: '1px solid rgba(255, 255, 255, 0.1)',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)',
                padding: 20,
                backdropFilter: 'blur(10px)',
                height: 'fit-content',
                maxHeight: 'calc(100vh - 200px)',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              {/* Sessions Header */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 16,
                  paddingBottom: 12,
                  borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
                }}
              >
                <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: '#e2e8f0' }}>Sessions</h3>
                <div
                  style={{
                    padding: '4px 10px',
                    borderRadius: 8,
                    fontSize: 11,
                    fontWeight: 600,
                    background:
                      activeCount > 0
                        ? 'linear-gradient(135deg, rgba(16, 163, 74, 0.2) 0%, rgba(34, 197, 94, 0.15) 100%)'
                        : 'rgba(15, 23, 42, 0.8)',
                    border: `1px solid ${activeCount > 0 ? 'rgba(34, 197, 94, 0.3)' : 'rgba(255, 255, 255, 0.1)'}`,
                    color: activeCount > 0 ? '#86efac' : '#9ca3af',
                  }}
                >
                  {activeCount} active
                </div>
              </div>

              {/* Sessions List */}
              <div
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  overflowX: 'hidden',
                  paddingRight: 4,
                }}
              >
                {sessions.length === 0 ? (
                  <div
                    style={{
                      padding: '40px 20px',
                      textAlign: 'center',
                      color: 'rgba(226, 232, 240, 0.6)',
                      fontSize: 13,
                    }}
                  >
                    <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.5 }}>📊</div>
                    <div>No sessions yet</div>
                    <div style={{ fontSize: 12, marginTop: 4, opacity: 0.7 }}>
                      Wait for ENTRY / EXIT crossings
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {sessions.map((s) => {
                      const status = s.exit_time ? 'closed' : 'active'
                      
                      // Calculate dwell time
                      let dwellSec: number | null = null
                      if (s.entry_time != null) {
                        const endTime = s.exit_time ?? Date.now() / 1000 // Use current time for active sessions
                        const dwellMs = (endTime - s.entry_time) * 1000
                        dwellSec = Math.max(0, Math.round(dwellMs / 1000))
                      }
                      return (
                        <div
                          key={s.session_id}
                          style={{
                            borderRadius: 12,
                            border: '1px solid rgba(255, 255, 255, 0.1)',
                            padding: 14,
                            background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.6) 0%, rgba(2, 6, 23, 0.8) 100%)',
                            transition: 'all 0.2s',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)'
                            e.currentTarget.style.transform = 'translateY(-1px)'
                            e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.3)'
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.1)'
                            e.currentTarget.style.transform = 'translateY(0)'
                            e.currentTarget.style.boxShadow = 'none'
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'flex-start',
                              marginBottom: 12,
                            }}
                          >
                            <div>
                              <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4, color: '#94a3b8' }}>
                                Customer ID
                              </div>
                              <div style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>
                                {s.session_id}
                              </div>
                            </div>
                            <div
                              style={{
                                fontSize: 11,
                                padding: '4px 10px',
                                borderRadius: 8,
                                background:
                                  status === 'active'
                                    ? 'linear-gradient(135deg, rgba(16, 163, 74, 0.2) 0%, rgba(34, 197, 94, 0.15) 100%)'
                                    : 'rgba(15, 23, 42, 0.8)',
                                border: `1px solid ${status === 'active' ? 'rgba(34, 197, 94, 0.4)' : 'rgba(255, 255, 255, 0.1)'}`,
                                color: status === 'active' ? '#86efac' : '#9ca3af',
                                fontWeight: 600,
                                textTransform: 'uppercase',
                                letterSpacing: '0.05em',
                              }}
                            >
                              {status}
                            </div>
                          </div>
                          <div
                            style={{
                              display: 'grid',
                              gridTemplateColumns: '1fr 1fr',
                              gap: 12,
                              fontSize: 12,
                            }}
                          >
                            <div>
                              <div style={{ opacity: 0.7, marginBottom: 4, color: '#94a3b8' }}>Entry Time</div>
                              <div style={{ color: '#e2e8f0', fontWeight: 500 }}>
                                {formatTime(s.entry_time)}
                              </div>
                            </div>
                            <div>
                              <div style={{ opacity: 0.7, marginBottom: 4, color: '#94a3b8' }}>Exit Time</div>
                              <div style={{ color: '#e2e8f0', fontWeight: 500 }}>
                                {formatTime(s.exit_time)}
                              </div>
                            </div>
                            <div style={{ gridColumn: '1 / -1' }}>
                              <div style={{ opacity: 0.7, marginBottom: 4, color: '#94a3b8' }}>Dwell Time</div>
                              <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: 14 }}>
                                {dwellSec != null ? (
                                  <>
                                    {dwellSec}s <span style={{ fontSize: 11, opacity: 0.7 }}>
                                      {status === 'active' ? 'so far' : 'in store'}
                                    </span>
                                  </>
                                ) : status === 'active' ? (
                                  <span style={{ color: '#86efac' }}>Active now…</span>
                                ) : (
                                  '—'
                                )}
                              </div>
                            </div>
                            {s.zone_visits && s.zone_visits.length > 0 && (
                              <div style={{ gridColumn: '1 / -1', marginTop: 8 }}>
                                <div style={{ opacity: 0.7, marginBottom: 6, color: '#94a3b8', fontSize: 11 }}>Zone Visits</div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                  {s.zone_visits.map((zv, idx) => {
                                    const duration = zv.exit_time 
                                      ? Math.round((zv.exit_time - zv.entry_time) * 100) / 100
                                      : null
                                    return (
                                      <div
                                        key={idx}
                                        style={{
                                          padding: '6px 10px',
                                          borderRadius: 6,
                                          background: 'rgba(100, 200, 255, 0.1)',
                                          border: '1px solid rgba(100, 200, 255, 0.2)',
                                          fontSize: 11,
                                        }}
                                      >
                                        <div style={{ color: '#64C8FF', fontWeight: 600 }}>
                                          {zv.zone_name}
                                        </div>
                                        {duration !== null && (
                                          <div style={{ color: '#94a3b8', marginTop: 2, fontSize: 10 }}>
                                            {duration}s
                                          </div>
                                        )}
                                        {duration === null && (
                                          <div style={{ color: '#86efac', marginTop: 2, fontSize: 10 }}>
                                            Active
                                          </div>
                                        )}
                                      </div>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      </div>
  )
}

export default App

