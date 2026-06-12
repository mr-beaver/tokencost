import SwiftUI

// MARK: – Trend chart

struct TrendChartView: View {
    let data: [TrendPoint]
    @State private var hoveredIndex: Int? = nil

    private var maxCost: Double { data.map(\.cost).max() ?? 0.0001 }
    private let orange = Color(red: 0.91, green: 0.35, blue: 0.08)

    // Chart dimensions
    private let chartHeight: CGFloat = 72
    private let yAxisWidth:  CGFloat = 36

    var body: some View {
        VStack(spacing: 0) {

            // ── Y-axis label (rotated) + chart area ──────────────────────────
            HStack(alignment: .bottom, spacing: 0) {

                // Y-axis: label + tick values
                ZStack {
                    // rotated axis title
                    Text("Cost ($)")
                        .font(.system(size: 7, weight: .semibold, design: .monospaced))
                        .foregroundColor(orange.opacity(0.75))
                        .rotationEffect(.degrees(-90))
                        .fixedSize()
                        .offset(x: -10)

                    // top value
                    VStack(spacing: 0) {
                        Text(yLabel(maxCost))
                            .font(.system(size: 9, weight: .semibold, design: .monospaced))
                            .foregroundColor(.primary.opacity(0.75))
                            .frame(height: 11)
                        Spacer()
                        Text(yLabel(maxCost / 2))
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.secondary.opacity(0.6))
                            .frame(height: 11)
                        Spacer()
                        Text("$0")
                            .font(.system(size: 9, weight: .semibold, design: .monospaced))
                            .foregroundColor(.primary.opacity(0.75))
                            .frame(height: 11)
                    }
                    .frame(width: yAxisWidth, height: chartHeight)
                    .padding(.leading, 4)
                }
                .frame(width: yAxisWidth, height: chartHeight)

                // ── Bars area ────────────────────────────────────────────────
                ZStack(alignment: .topLeading) {

                    // horizontal grid lines at 100%, 50%, 0%
                    VStack(spacing: 0) {
                        Color.secondary.opacity(0.15).frame(height: 0.5)
                        Spacer()
                        Color.secondary.opacity(0.10).frame(height: 0.5)
                        Spacer()
                        Color.secondary.opacity(0.15).frame(height: 0.5)
                    }

                    GeometryReader { geo in
                        let count   = max(data.count, 1)
                        let spacing: CGFloat = data.count > 14 ? 1 : 2
                        let barW    = max((geo.size.width - CGFloat(count - 1) * spacing) / CGFloat(count), 2)

                        HStack(alignment: .bottom, spacing: spacing) {
                            ForEach(data.indices, id: \.self) { i in
                                let frac  = maxCost > 0 ? CGFloat(data[i].cost / maxCost) : 0
                                let isHov = hoveredIndex == i

                                ZStack(alignment: .top) {
                                    // bar
                                    VStack(spacing: 0) {
                                        Spacer(minLength: 0)
                                        RoundedRectangle(cornerRadius: 2)
                                            .fill(LinearGradient(
                                                colors: isHov
                                                    ? [Color(red:0.65, green:0.12, blue:0.0),
                                                       Color(red:1.0,  green:0.52, blue:0.1)]
                                                    : [Color(red:0.85, green:0.28, blue:0.04),
                                                       Color(red:0.99, green:0.62, blue:0.22)],
                                                startPoint: .bottom, endPoint: .top))
                                            .opacity(isHov ? 1.0 : Double(0.42 + 0.58 * frac))
                                            .frame(width: barW)
                                            .frame(height: max(frac * (geo.size.height - 1), 2))
                                    }
                                    .frame(width: barW, height: geo.size.height)

                                    // tooltip on hover
                                    if isHov {
                                        let isRightHalf = i >= data.count / 2
                                        tooltipView(for: i)
                                            .offset(x: isRightHalf ? -(140 - barW / 2) : 0,
                                                    y: -(72 + 4))
                                            .zIndex(20)
                                            .fixedSize()
                                    }
                                }
                                .frame(width: barW)
                                .contentShape(Rectangle()
                                    .size(CGSize(width: barW, height: geo.size.height + 50)))
                                .onHover { inside in hoveredIndex = inside ? i : nil }
                            }
                        }
                    }
                    .frame(height: chartHeight)
                }
            }

            // ── X-axis tick labels ────────────────────────────────────────────
            HStack(spacing: 0) {
                Spacer().frame(width: yAxisWidth)
                GeometryReader { geo in
                    let count   = max(data.count, 1)
                    let spacing: CGFloat = data.count > 14 ? 1 : 2
                    let barW    = max((geo.size.width - CGFloat(count - 1) * spacing) / CGFloat(count), 2)
                    HStack(alignment: .top, spacing: spacing) {
                        ForEach(data.indices, id: \.self) { i in
                            let isHov = hoveredIndex == i
                            let show  = data.count <= 10 ? true
                                      : data.count <= 18 ? i % 2 == 0
                                      : i % 3 == 0
                            Text(show ? shortLabel(data[i].label) : "")
                                .font(.system(size: 8,
                                              weight: isHov ? .bold : .regular,
                                              design: .monospaced))
                                .foregroundColor(isHov ? orange : .secondary.opacity(0.5))
                                .frame(width: barW, alignment: .center)
                                .lineLimit(1)
                        }
                    }
                }
            }
            .frame(height: 13)
            .padding(.top, 2)

            // ── X-axis title ─────────────────────────────────────────────────
            HStack(spacing: 0) {
                Spacer().frame(width: yAxisWidth)
                Text("Time →")
                    .font(.system(size: 7, weight: .semibold, design: .monospaced))
                    .foregroundColor(orange.opacity(0.65))
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.top, 1)
        }
    }

    // ── Tooltip ───────────────────────────────────────────────────────────────

    @ViewBuilder
    private func tooltipView(for i: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            // time row
            HStack(spacing: 5) {
                Image(systemName: "clock.fill")
                    .font(.system(size: 9))
                    .foregroundColor(orange.opacity(0.9))
                Text(fullTimeLabel(data[i].label))
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundColor(.white.opacity(0.9))
            }
            // divider
            Rectangle()
                .fill(Color.white.opacity(0.15))
                .frame(height: 0.5)
            // cost — big
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(costLabel(data[i].cost))
                    .font(.system(size: 16, weight: .bold, design: .monospaced))
                    .foregroundColor(Color(red: 1.0, green: 0.85, blue: 0.25))
                Text("spent")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.white.opacity(0.5))
            }
            // requests
            HStack(spacing: 4) {
                Image(systemName: "arrow.up.arrow.down")
                    .font(.system(size: 8))
                    .foregroundColor(.white.opacity(0.45))
                Text("\(data[i].reqs) requests")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.white.opacity(0.6))
            }
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(red: 0.10, green: 0.10, blue: 0.13).opacity(0.97))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(orange.opacity(0.7), lineWidth: 1)
                )
                .shadow(color: .black.opacity(0.5), radius: 12, x: 0, y: 4)
        )
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private func yLabel(_ v: Double) -> String {
        if v >= 10  { return String(format: "$%.0f", v) }
        if v >= 1   { return String(format: "$%.1f", v) }
        if v >= 0.1 { return String(format: "$%.2f", v) }
        return String(format: "$%.3f", v)
    }

    private func costLabel(_ cost: Double) -> String {
        if cost >= 10  { return String(format: "$%.2f", cost) }
        if cost >= 1   { return String(format: "$%.2f", cost) }
        if cost >= 0.1 { return String(format: "$%.3f", cost) }
        return String(format: "$%.4f", cost)
    }

    private func shortLabel(_ s: String) -> String {
        if s.count == 10 && s.contains("-") {
            let parts = s.split(separator: "-")
            if parts.count == 3 {
                let months = ["","Jan","Feb","Mar","Apr","May","Jun",
                              "Jul","Aug","Sep","Oct","Nov","Dec"]
                let m = Int(parts[1]) ?? 0
                return "\(parts[2])/\(m < months.count ? String(m) : String(parts[1]))"
            }
        }
        if s.count <= 2, let h = Int(s) {
            return h == 0 ? "12a" : h < 12 ? "\(h)a" : h == 12 ? "12p" : "\(h-12)p"
        }
        return s
    }

    private func fullTimeLabel(_ s: String) -> String {
        if s.count == 10 && s.contains("-") {
            let parts = s.split(separator: "-")
            if parts.count == 3 {
                let months = ["","Jan","Feb","Mar","Apr","May","Jun",
                              "Jul","Aug","Sep","Oct","Nov","Dec"]
                let m = Int(parts[1]) ?? 0
                let month = m < months.count ? months[m] : String(parts[1])
                return "\(month) \(parts[2]), \(parts[0])"
            }
        }
        if s.count <= 2, let h = Int(s) {
            return String(format: "%02d:00 – %02d:00", h, (h + 1) % 24)
        }
        return s
    }
}

// MARK: – Shared styles

private let monoSm   = Font.system(size: 12, design: .monospaced)
private let monoMd   = Font.system(size: 13, design: .monospaced)
private let hdrFont  = Font.system(size: 10, weight: .semibold, design: .monospaced)
private var altBg: Color { Color(NSColor.alternatingContentBackgroundColors[1]).opacity(0.5) }
private var hdrBg: Color { Color(NSColor.controlBackgroundColor).opacity(0.7) }
private let accentOrange = Color(red: 0.91, green: 0.35, blue: 0.08)

private func shortModel(_ s: String) -> String {
    s.replacingOccurrences(of: "claude-", with: "")
     .replacingOccurrences(of: "-20[0-9]{6}$", with: "", options: .regularExpression)
     .replacingOccurrences(of: "gpt-", with: "")
     .replacingOccurrences(of: "gemini-", with: "gem-")
     .replacingOccurrences(of: "groq/", with: "")
     .replacingOccurrences(of: "llama-", with: "ll-")
}

private func shortProject(_ p: String) -> String {
    let parts = p.split(separator: "/")
    if parts.count >= 2 { return parts.suffix(2).joined(separator: "/") }
    return p
}

private func fmtTokens(_ n: Int) -> String {
    if n >= 1000 { return String(format: "%.1fk", Double(n) / 1000) }
    return "\(n)"
}

// MARK: – Button styles

struct PeriodButtonStyle: ButtonStyle {
    let isSelected: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: isSelected ? .bold : .medium, design: .monospaced))
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(isSelected ? accentOrange : Color(NSColor.controlBackgroundColor))
            .foregroundColor(isSelected ? .white : Color.primary.opacity(0.7))
            .cornerRadius(6)
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

struct TabButtonStyle: ButtonStyle {
    let isSelected: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: isSelected ? .bold : .medium, design: .monospaced))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(isSelected
                ? accentOrange.opacity(0.15)
                : Color(NSColor.controlBackgroundColor).opacity(0.4))
            .foregroundColor(isSelected ? accentOrange : Color.primary.opacity(0.65))
            .cornerRadius(5)
            .overlay(RoundedRectangle(cornerRadius: 5)
                .stroke(isSelected ? accentOrange.opacity(0.5) : Color.secondary.opacity(0.15), lineWidth: 1))
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

// MARK: – Row helpers

private struct TableRow: View {
    let label: String
    let value1: String
    let value2: String
    let value3: String
    let color1: Color
    let color2: Color
    let alt: Bool
    let barFrac: Double
    let barColor: Color

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 5) {
                if barFrac > 0 {
                    RoundedRectangle(cornerRadius: 2).fill(barColor)
                        .frame(width: max(CGFloat(barFrac) * 32, 3), height: 7)
                }
                Text(label).font(monoMd).lineLimit(1).foregroundColor(.primary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Text(value1).font(monoSm).foregroundColor(color1).frame(width: 56, alignment: .trailing)
            Text(value2).font(monoSm).foregroundColor(color2).frame(width: 44, alignment: .trailing)
            Text(value3).font(monoSm).foregroundColor(.secondary).frame(width: 40, alignment: .trailing)
        }
        .padding(.horizontal, 14).padding(.vertical, 7)
        .background(alt ? altBg : Color.clear)
    }
}

private struct ColHeader: View {
    let c1: String; let c2: String; let c3: String
    var body: some View {
        HStack(spacing: 0) {
            Text("").frame(maxWidth: .infinity)
            Text(c1).frame(width: 56, alignment: .trailing)
            Text(c2).frame(width: 44, alignment: .trailing)
            Text(c3).frame(width: 40, alignment: .trailing)
        }
        .font(hdrFont).foregroundColor(.secondary).tracking(0.3)
        .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)
    }
}

private struct StatCell: View {
    let label: String; let value: String; var color: Color = .primary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundColor(.secondary).tracking(0.5)
            Text(value)
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundColor(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct BigStat: View {
    let label: String; let value: String; var sub: String = ""; var color: Color = .primary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundColor(.secondary).tracking(0.5)
            Text(value)
                .font(.system(size: 20, weight: .bold, design: .monospaced))
                .foregroundColor(color)
            if !sub.isEmpty {
                Text(sub).font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: – Optimizer pill row

private struct OptPill: View {
    let label: String
    let value: String
    let color: Color
    var sub: String = ""

    var body: some View {
        VStack(spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundColor(.secondary)
                .tracking(0.5)
            Text(value)
                .font(.system(size: 16, weight: .bold, design: .monospaced))
                .foregroundColor(color)
            if !sub.isEmpty {
                Text(sub)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color(NSColor.controlBackgroundColor).opacity(0.6))
        .cornerRadius(8)
        .overlay(RoundedRectangle(cornerRadius: 8)
            .stroke(color.opacity(0.25), lineWidth: 1))
    }
}

// MARK: – Main view

struct MenuBarView: View {
    @ObservedObject var model: StatsModel

    private let catColors: [String: Color] = [
        "code": .blue, "bash": Color(red:0.49,green:0.23,blue:0.87),
        "agent": .orange, "web": Color(red:0.04,green:0.57,blue:0.71),
        "plan": .green, "search": .gray, "mcp": Color(red:0.58,green:0.20,blue:0.92),
        "chat": Color(red:0.30,green:0.30,blue:0.35),
    ]
    private let catLabels: [String: String] = [
        "code": "Coding", "bash": "Shell", "agent": "Agent",
        "web": "Web", "plan": "Planning", "search": "Exploration",
        "mcp": "MCP", "chat": "Chat",
    ]

    var body: some View {
        VStack(spacing: 0) {
            headerRow
            Divider()
            numbersBlock
            Divider()
            periodRow
            Divider()
            tabGrid
            Divider()
            contentArea
                .id("\(model.period.rawValue)-\(model.tab.rawValue)")
            Divider()
            syncBar
            versionBanner
            Divider()
            footerRow
        }
        .frame(width: 340)
        .background(Color(NSColor.windowBackgroundColor))
        .task { model.refresh() }
    }

    // MARK: Header

    private var headerRow: some View {
        HStack {
            Text("TokenCost")
                .font(.system(size: 12, weight: .bold, design: .monospaced))
                .foregroundColor(.primary.opacity(0.8))
            Spacer()
            if !model.proxyOK {
                Image(systemName: "exclamationmark.circle.fill").foregroundColor(.red).font(.system(size: 12))
            }
            Button { model.refresh() } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 12, weight: .medium)).foregroundColor(.secondary)
            }.buttonStyle(.plain)
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
    }

    // MARK: Numbers

    private var numbersBlock: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(String(format: "$%.2f", model.periodCost))
                    .font(.system(size: 32, weight: .bold, design: .monospaced))
                HStack(spacing: 6) {
                    Image(systemName: "clock").font(.system(size: 10)).foregroundColor(.secondary)
                    Text("Today: " + String(format: "$%.4f", model.todayCost))
                        .font(.system(size: 12, design: .monospaced)).foregroundColor(.secondary)
                    if !model.comparison.isEmpty {
                        Text(model.comparison)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(model.comparisonPositive ? accentOrange : .green)
                    }
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text("\(model.totalReqs.formatted()) calls")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundColor(.primary.opacity(0.8))
                Text("\(model.sessionCount) sessions")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
    }

    // MARK: Period

    private var periodRow: some View {
        HStack(spacing: 6) {
            ForEach(AppPeriod.allCases) { p in
                Button(p.label) { model.setPeriod(p) }.buttonStyle(PeriodButtonStyle(isSelected: model.period == p))
            }
            Spacer()
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    // MARK: Tab grid — 4 + 3 layout

    private var tabGrid: some View {
        let all = AppTab.allCases  // 8 tabs — 4+4
        return VStack(spacing: 4) {
            HStack(spacing: 4) {
                ForEach(all.prefix(4)) { t in tabBtn(t) }
            }
            HStack(spacing: 4) {
                ForEach(all.dropFirst(4)) { t in tabBtn(t) }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
    }

    private func tabBtn(_ t: AppTab) -> some View {
        Button(t.rawValue) { model.setTab(t) }.buttonStyle(TabButtonStyle(isSelected: model.tab == t))
    }

    // MARK: Content router

    @ViewBuilder
    private var contentArea: some View {
        switch model.tab {
        case .trend:     trendView
        case .tasks:     tasksView
        case .models:    modelsView
        case .projects:  projectsView
        case .cache:     cacheView
        case .tools:     toolsView
        case .optimizer: optimizerView
        case .logs:      logsView
        }
    }

    // MARK: – Trend

    private var trendView: some View {
        VStack(spacing: 10) {
            if model.trendData.isEmpty {
                Text("No data yet").font(monoSm).foregroundColor(.secondary)
                    .frame(height: 80).frame(maxWidth: .infinity)
            } else {
                TrendChartView(data: model.trendData)
            }
            Divider()
            HStack(spacing: 0) {
                StatCell(label: "AVG/DAY",  value: String(format: "$%.2f", model.avgPerDay))
                StatCell(label: "PEAK",     value: model.peakCost > 0 ? String(format: "$%.2f", model.peakCost) : "—")
                StatCell(label: "CACHE",    value: String(format: "%.0f%%", model.cacheHit),
                         color: model.cacheHit >= 60 ? .green : accentOrange)
                StatCell(label: "PROJ/MO",  value: String(format: "$%.0f", model.monthProj))
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }

    // MARK: – Tasks

    private var tasksView: some View {
        VStack(spacing: 0) {
            ColHeader(c1: "Cost", c2: "Turns", c3: "1-shot")
            let top = Array(model.activityData.prefix(7))
            let maxC = top.compactMap(\.cost).max() ?? 0.0001
            if top.isEmpty {
                Text("No activity").font(monoSm).foregroundColor(.secondary).padding(.vertical, 16)
            } else {
                ForEach(top.indices, id: \.self) { i in
                    let item = top[i]
                    let frac = (item.cost ?? 0) / maxC
                    let c    = catColors[item.category] ?? .secondary
                    let lbl  = catLabels[item.category] ?? item.category.capitalized
                    let pct1 = item.one_shot_pct.map { "\($0)%" } ?? "—"
                    TableRow(
                        label: lbl,
                        value1: item.cost.map { String(format: "$%.2f", $0) } ?? "—",
                        value2: "\(item.reqs)",
                        value3: pct1,
                        color1: accentOrange, color2: .secondary,
                        alt: i % 2 == 1,
                        barFrac: frac, barColor: c
                    )
                }
            }
        }
    }

    // MARK: – Models

    private var modelsView: some View {
        VStack(spacing: 0) {
            ColHeader(c1: "Cost", c2: "Cache%", c3: "1-shot")
            let top = Array(model.byModel.prefix(7))
            if top.isEmpty {
                Text("No model data").font(monoSm).foregroundColor(.secondary).padding(.vertical, 16)
            } else {
                let maxC = top.compactMap(\.cost).max() ?? 0.0001
                ForEach(top.indices, id: \.self) { i in
                    let m    = top[i]
                    let name = shortModel(m.model)
                    let frac = (m.cost ?? 0) / maxC
                    let cHit = m.cache_hit_rate ?? 0
                    let pct1 = m.one_shot_pct.map { "\($0)%" } ?? "—"
                    let cacheC: Color = cHit >= 70 ? .green : cHit >= 40 ? .blue : .secondary
                    TableRow(
                        label: name,
                        value1: m.cost.map { String(format: "$%.2f", $0) } ?? "—",
                        value2: cHit > 0 ? String(format: "%.0f%%", cHit) : "—",
                        value3: pct1,
                        color1: accentOrange, color2: cacheC,
                        alt: i % 2 == 1,
                        barFrac: frac, barColor: .blue
                    )
                }
            }
        }
    }

    // MARK: – Projects

    private var projectsView: some View {
        VStack(spacing: 0) {
            HStack {
                Text("TOP SESSIONS")
                    .font(hdrFont).foregroundColor(.secondary).tracking(0.5)
                Spacer()
                Text("cost  calls").font(hdrFont).foregroundColor(.secondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

            let topS = Array(model.topSessions.prefix(4))
            if topS.isEmpty {
                Text("No sessions yet").font(monoSm).foregroundColor(.secondary).padding(.vertical, 10)
            } else {
                ForEach(topS.indices, id: \.self) { i in
                    let s = topS[i]
                    HStack(spacing: 0) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.date).font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
                            Text(shortProject(s.path)).font(monoSm).foregroundColor(.primary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Text(String(format: "$%.2f", s.cost)).font(monoSm).foregroundColor(accentOrange).frame(width: 54, alignment: .trailing)
                        Text("\(s.calls)").font(monoSm).foregroundColor(.secondary).frame(width: 40, alignment: .trailing)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(i % 2 == 1 ? altBg : Color.clear)
                }
            }

            Divider()

            HStack {
                Text("BY PROJECT").font(hdrFont).foregroundColor(.secondary).tracking(0.5)
                Spacer()
                Text("cost  sess").font(hdrFont).foregroundColor(.secondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

            let projs = Array(model.byProject.prefix(4))
            let maxPC = projs.map(\.cost).max() ?? 0.0001
            ForEach(projs.indices, id: \.self) { i in
                let p = projs[i]
                HStack(spacing: 0) {
                    HStack(spacing: 5) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Color.blue.opacity(0.7))
                            .frame(width: max(CGFloat(p.cost/maxPC)*28, 3), height: 7)
                        Text(shortProject(p.path)).font(monoSm).foregroundColor(.primary).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Text(String(format: "$%.2f", p.cost)).font(monoSm).foregroundColor(accentOrange).frame(width: 54, alignment: .trailing)
                    Text("\(p.sessions)").font(monoSm).foregroundColor(.secondary).frame(width: 40, alignment: .trailing)
                }
                .padding(.horizontal, 14).padding(.vertical, 6)
                .background(i % 2 == 1 ? altBg : Color.clear)
            }
        }
    }

    // MARK: – Cache

    private var cacheView: some View {
        VStack(spacing: 12) {
            HStack(spacing: 0) {
                BigStat(label: "SAVED BY CACHE",
                        value: String(format: "$%.4f", model.cacheSaved),
                        sub: "vs paying full input price",
                        color: .green)
                BigStat(label: "HIT RATE",
                        value: String(format: "%.0f%%", model.cacheHit),
                        color: model.cacheHit >= 60 ? .green : accentOrange)
            }
            HStack(spacing: 6) {
                costPill(label: "OUTPUT", value: String(format: "$%.3f", model.outputCost), color: .red)
                costPill(label: "INPUT",  value: String(format: "$%.3f", model.inputCost),  color: .secondary)
                costPill(label: "WRITE",  value: String(format: "$%.3f", model.cacheWriteCost), color: .blue)
                costPill(label: "READ",   value: String(format: "$%.3f", model.cacheReadCost),  color: .green)
            }
            if model.haikuSavings > 0.01 {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.down.circle.fill").foregroundColor(.blue).font(.system(size: 13))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Haiku equivalent: " + String(format: "$%.2f", model.haikuEquiv))
                            .font(monoSm).foregroundColor(.secondary)
                        Text(String(format: "Save $%.2f switching to Haiku for simple tasks", model.haikuSavings))
                            .font(.system(size: 11, design: .monospaced)).foregroundColor(.secondary)
                    }
                }
                .padding(.vertical, 2)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }

    private func costPill(label: String, value: String, color: Color) -> some View {
        VStack(spacing: 3) {
            Text(label).font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.secondary)
            Text(value).font(.system(size: 12, weight: .bold, design: .monospaced)).foregroundColor(color)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 7)
        .background(Color(NSColor.controlBackgroundColor).opacity(0.5))
        .cornerRadius(6)
    }

    // MARK: – Tools

    private var toolsView: some View {
        VStack(spacing: 0) {
            HStack {
                Text("CORE TOOLS").font(hdrFont).foregroundColor(.secondary).tracking(0.5)
                Spacer()
                Text("calls").font(hdrFont).foregroundColor(.secondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

            let tools  = Array(model.coreTools.prefix(6))
            let maxT   = tools.map(\.count).max() ?? 1
            if tools.isEmpty {
                Text("No tool data").font(monoSm).foregroundColor(.secondary).padding(.vertical, 10)
            } else {
                ForEach(tools.indices, id: \.self) { i in
                    let t = tools[i]
                    HStack(spacing: 0) {
                        HStack(spacing: 5) {
                            RoundedRectangle(cornerRadius: 2).fill(Color.blue.opacity(0.7))
                                .frame(width: max(CGFloat(t.count)/CGFloat(maxT)*32, 3), height: 7)
                            Text(t.name).font(monoMd).foregroundColor(.primary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Text("\(t.count)").font(monoSm).foregroundColor(.secondary).frame(width: 52, alignment: .trailing)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(i % 2 == 1 ? altBg : Color.clear)
                }
            }

            if !model.mcpServers.isEmpty {
                Divider()
                HStack {
                    Text("MCP SERVERS").font(hdrFont).foregroundColor(Color(red:0.58,green:0.20,blue:0.92)).tracking(0.5)
                    Spacer()
                    Text("calls").font(hdrFont).foregroundColor(.secondary)
                }
                .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

                ForEach(model.mcpServers.prefix(4).indices, id: \.self) { i in
                    let s = model.mcpServers[i]
                    HStack {
                        Text(s.name).font(monoMd).foregroundColor(Color(red:0.58,green:0.20,blue:0.92)).lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text("\(s.count)").font(monoSm).foregroundColor(.secondary).frame(width: 52, alignment: .trailing)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(i % 2 == 1 ? altBg : Color.clear)
                }
            }
        }
    }

    // MARK: – Optimizer

    private var optimizerView: some View {
        VStack(spacing: 0) {

            // ── KPI row ──────────────────────────────────────────────────────
            HStack(spacing: 6) {
                let totalSaved = model.routingSaved + model.cacheSaved
                OptPill(label: "TOTAL SAVED",
                        value: String(format: "$%.2f", totalSaved),
                        color: .green)
                OptPill(label: "CACHE SAVED",
                        value: String(format: "$%.2f", model.cacheSaved),
                        color: Color(red:0.05, green:0.65, blue:0.35),
                        sub: String(format: "%.0f%% hit", model.cacheHit))
                OptPill(label: "ROUTING",
                        value: String(format: "$%.2f", model.routingSaved),
                        color: .blue,
                        sub: "\(model.routingRequests) reqs")
            }
            .padding(.horizontal, 12).padding(.top, 10).padding(.bottom, 8)

            Divider()

            // ── Smart Routing section ────────────────────────────────────────
            HStack {
                Text("⚡ SMART ROUTING")
                    .font(hdrFont).foregroundColor(.blue).tracking(0.5)
                Spacer()
                Text("Sonnet/Opus → Haiku")
                    .font(.system(size: 10, design: .monospaced)).foregroundColor(.secondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 6).background(hdrBg)

            if model.routingRequests > 0 {
                VStack(spacing: 0) {
                    // Tokens row
                    HStack(spacing: 0) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("AVG IN TOK").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.secondary)
                            Text(fmtTokens(model.routingAvgIn))
                                .font(.system(size: 15, weight: .bold, design: .monospaced)).foregroundColor(.primary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("AVG OUT TOK").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.secondary)
                            Text(fmtTokens(model.routingAvgOut))
                                .font(.system(size: 15, weight: .bold, design: .monospaced)).foregroundColor(.primary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        VStack(alignment: .trailing, spacing: 2) {
                            Text("ACTUAL PAID").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.secondary)
                            Text(String(format: "$%.2f", model.routingActualCost))
                                .font(.system(size: 15, weight: .bold, design: .monospaced)).foregroundColor(accentOrange)
                        }
                        .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 8)

                    // Effort breakdown
                    if !model.effortCounts.isEmpty {
                        Divider()
                        HStack {
                            Text("EFFORT BREAKDOWN")
                                .font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.secondary)
                            Spacer()
                        }
                        .padding(.horizontal, 14).padding(.top, 6).padding(.bottom, 3)

                        let effortOrder = ["standard", "low", "medium", "high", "xhigh"]
                        let effortColors: [String: Color] = [
                            "standard": .secondary,
                            "low": .blue,
                            "medium": accentOrange,
                            "high": .red,
                            "xhigh": Color(red:0.7, green:0.0, blue:0.9),
                        ]
                        let total = model.effortCounts.values.reduce(0, +)
                        let maxE = model.effortCounts.values.max() ?? 1

                        ForEach(effortOrder.filter { model.effortCounts[$0] != nil }, id: \.self) { e in
                            let cnt = model.effortCounts[e] ?? 0
                            let pct = total > 0 ? Double(cnt) / Double(total) * 100 : 0
                            let c = effortColors[e] ?? .secondary
                            HStack(spacing: 8) {
                                Text(e)
                                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                                    .foregroundColor(.primary)
                                    .frame(width: 64, alignment: .leading)
                                GeometryReader { geo in
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(c.opacity(0.7))
                                        .frame(width: max(CGFloat(cnt) / CGFloat(maxE) * geo.size.width, 3), height: 8)
                                        .frame(maxHeight: .infinity)
                                }
                                .frame(height: 14)
                                Text("\(cnt)")
                                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                                    .foregroundColor(c)
                                    .frame(width: 42, alignment: .trailing)
                                Text(String(format: "%.0f%%", pct))
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundColor(.secondary)
                                    .frame(width: 32, alignment: .trailing)
                            }
                            .padding(.horizontal, 14).padding(.vertical, 4)
                        }
                    }
                }
            } else {
                Text("Smart routing not active for this period")
                    .font(monoSm).foregroundColor(.secondary)
                    .frame(maxWidth: .infinity).padding(.vertical, 14)
            }

            Divider()

            // ── Open full report link ─────────────────────────────────────────
            HStack {
                Spacer()
                Button("View full Optimizer report ↗") {
                    NSWorkspace.shared.open(URL(string: "http://localhost:8082/dashboard#optimizer")!)
                }
                .buttonStyle(.plain)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.accentColor)
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
        }
    }

    // MARK: – Logs

    private var logsView: some View {
        let dir = Bundle.main.bundlePath
            .components(separatedBy: "/menubar/").first
            ?? (Bundle.main.bundlePath as NSString).deletingLastPathComponent

        let logFiles: [(name: String, path: String)] = [
            ("proxy.log",       dir + "/proxy.log"),
            ("proxy-error.log", dir + "/proxy-error.log"),
            ("sync.log",        dir + "/sync.log"),
        ]

        return VStack(alignment: .leading, spacing: 0) {
            // Install dir
            HStack {
                Text("INSTALL DIR").font(hdrFont).foregroundColor(.secondary).tracking(0.5)
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(dir, forType: .string)
                } label: {
                    Text("copy").font(hdrFont).foregroundColor(accentOrange)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

            HStack(spacing: 6) {
                Text(dir)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.primary.opacity(0.8))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: dir))
                } label: {
                    Image(systemName: "folder")
                        .font(.system(size: 11))
                        .foregroundColor(accentOrange)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 14).padding(.vertical, 8)

            Divider()

            // Log files
            HStack {
                Text("LOG FILES").font(hdrFont).foregroundColor(.secondary).tracking(0.5)
                Spacer()
            }
            .padding(.horizontal, 14).padding(.vertical, 5).background(hdrBg)

            ForEach(logFiles.indices, id: \.self) { i in
                let f = logFiles[i]
                let exists = FileManager.default.fileExists(atPath: f.path)
                let size: String = {
                    guard exists,
                          let attr = try? FileManager.default.attributesOfItem(atPath: f.path),
                          let bytes = attr[.size] as? Int64 else { return "—" }
                    if bytes < 1024 { return "\(bytes) B" }
                    if bytes < 1024*1024 { return String(format: "%.1f KB", Double(bytes)/1024) }
                    return String(format: "%.1f MB", Double(bytes)/1024/1024)
                }()

                HStack(spacing: 0) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) {
                            Circle()
                                .fill(exists ? Color.green : Color.secondary.opacity(0.4))
                                .frame(width: 6, height: 6)
                            Text(f.name)
                                .font(.system(size: 12, weight: .medium, design: .monospaced))
                                .foregroundColor(exists ? .primary : .secondary)
                        }
                        Text(f.path)
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.secondary.opacity(0.7))
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    Text(size)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                        .frame(width: 52, alignment: .trailing)

                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(f.path, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.system(size: 10))
                            .foregroundColor(accentOrange.opacity(0.8))
                    }
                    .buttonStyle(.plain)
                    .padding(.leading, 8)
                }
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(i % 2 == 1 ? altBg : Color.clear)
            }
        }
    }

    // MARK: Sync bar

    private var syncBar: some View {
        HStack(spacing: 8) {
            Button {
                model.syncNow()
            } label: {
                HStack(spacing: 4) {
                    if model.isSyncing {
                        ProgressView().scaleEffect(0.6).frame(width: 12, height: 12)
                    } else {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 11, weight: .semibold))
                    }
                    Text(model.isSyncing ? "Syncing…" : "Sync Logs")
                        .font(.system(size: 12, weight: .semibold, design: .monospaced))
                }
                .padding(.horizontal, 11).padding(.vertical, 6)
                .background(model.isSyncing ? accentOrange.opacity(0.4) : accentOrange)
                .foregroundColor(.white)
                .cornerRadius(6)
            }
            .buttonStyle(.plain)
            .disabled(model.isSyncing)

            VStack(alignment: .leading, spacing: 1) {
                if !model.syncResult.isEmpty {
                    Text(model.syncResult)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.green).lineLimit(1)
                } else if !model.lastSyncAgo.isEmpty {
                    Text("last: \(model.lastSyncAgo)")
                        .font(.system(size: 11, design: .monospaced)).foregroundColor(.secondary)
                } else {
                    Text("Cline · Roo · Desktop · CLI")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(Color.secondary.opacity(0.6))
                }
            }
            Spacer()
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(accentOrange.opacity(0.06))
    }

    // MARK: – Version banner

    @ViewBuilder
    private var versionBanner: some View {
        if !model.versionUpToDate, let latest = model.latestVersion {
            HStack(spacing: 8) {
                Image(systemName: "arrow.down.circle.fill")
                    .foregroundColor(.white).font(.system(size: 13))
                VStack(alignment: .leading, spacing: 1) {
                    Text("Update available: v\(model.currentVersion) → v\(latest)")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundColor(.white)
                }
                Spacer()
                Button {
                    model.updateNow()
                } label: {
                    if model.isUpdating {
                        HStack(spacing: 4) {
                            ProgressView().scaleEffect(0.55).frame(width: 12, height: 12)
                                .colorScheme(.dark)
                            Text("Updating…")
                                .font(.system(size: 11, weight: .bold, design: .monospaced))
                                .foregroundColor(.white)
                        }
                    } else {
                        Text("Update now")
                            .font(.system(size: 11, weight: .bold, design: .monospaced))
                            .foregroundColor(accentOrange)
                    }
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Color.white.opacity(0.15))
                .cornerRadius(5)
                .disabled(model.isUpdating)
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(accentOrange)

            if let res = model.updateResult {
                Text(res)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(res.hasPrefix("✓") ? .green : .red)
                    .padding(.horizontal, 14).padding(.vertical, 4)
            }
        }
    }

    // MARK: Footer

    private var footerRow: some View {
        HStack(spacing: 8) {
            ZStack {
                Circle().strokeBorder(model.gradeColor, lineWidth: 2).frame(width: 36, height: 36)
                VStack(spacing: 0) {
                    Text(model.grade)
                        .font(.system(size: 18, weight: .black, design: .monospaced))
                        .foregroundColor(model.gradeColor)
                    Text("\(model.gradeScore)")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundColor(model.gradeColor.opacity(0.7))
                }
            }
            HStack(spacing: 12) {
                HStack(spacing: 4) {
                    Text("v\(model.currentVersion)")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundColor(model.versionUpToDate ? .secondary : accentOrange)
                    Button {
                        Task { await model.fetchVersion() }
                    } label: {
                        Text(model.versionUpToDate ? "✓" : "⬆")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(model.versionUpToDate ? Color.green.opacity(0.8) : accentOrange)
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                Button("Dashboard ↗") {
                    NSWorkspace.shared.open(URL(string: "http://localhost:8082/dashboard")!)
                }
                .buttonStyle(.plain).font(.system(size: 9, design: .monospaced)).foregroundColor(.accentColor)
                Button("Quit") { NSApplication.shared.terminate(nil) }
                    .buttonStyle(.plain).font(.system(size: 9, design: .monospaced)).foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }
}
