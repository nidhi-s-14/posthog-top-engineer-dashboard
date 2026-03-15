async function loadJson(path) {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`)
  }
  return response.json()
}

const interactionState = {
  pinnedEngineer: null,
  hoveredEngineer: null,
}

const dashboardCache = {
  scoreboard: null,
  balanceView: null,
  throughput: null,
  mergeTime: null,
  changeSurface: null,
}

function setText(id, value) {
  const element = document.getElementById(id)
  if (element) {
    element.textContent = value
  }
}

function tooltipEl() {
  return document.getElementById("chart-tooltip")
}

function activeEngineer() {
  return interactionState.pinnedEngineer || interactionState.hoveredEngineer
}

function showTooltip(event, html) {
  const tooltip = tooltipEl()
  if (!tooltip) {
    return
  }
  tooltip.hidden = false
  tooltip.innerHTML = html
  moveTooltip(event)
}

function moveTooltip(event) {
  const tooltip = tooltipEl()
  if (!tooltip || tooltip.hidden) {
    return
  }
  tooltip.style.left = `${event.clientX + 14}px`
  tooltip.style.top = `${event.clientY + 14}px`
}

function hideTooltip() {
  const tooltip = tooltipEl()
  if (tooltip) {
    tooltip.hidden = true
  }
}

function syncInteractionStyles() {
  const selected = activeEngineer()

  document.querySelectorAll(".scoreboard-row[data-engineer], .interactive-mark[data-engineer]").forEach((element) => {
    const engineer = element.getAttribute("data-engineer")
    element.classList.toggle("is-dimmed", Boolean(selected && engineer !== selected))
    element.classList.toggle("is-selected", Boolean(selected && engineer === selected))
  })
}

function setHoveredEngineer(engineer) {
  interactionState.hoveredEngineer = engineer
  syncInteractionStyles()
}

function togglePinnedEngineer(engineer) {
  interactionState.pinnedEngineer = interactionState.pinnedEngineer === engineer ? null : engineer
  hideTooltip()
  rerenderInteractiveViews()
}

function rerenderInteractiveViews() {
  renderScoreboard(dashboardCache.scoreboard)
  renderBalanceChart(dashboardCache.balanceView)
  renderThroughputChart(dashboardCache.throughput)
  renderMergeTimeChart(dashboardCache.mergeTime)
  renderChangeSurfaceChart(dashboardCache.changeSurface)
}

function buildVisibleChartData(engineers, limit, predicate = null) {
  const filteredEngineers = predicate ? engineers.filter(predicate) : engineers.slice()
  const pinnedEngineer = interactionState.pinnedEngineer

  if (!pinnedEngineer) {
    return filteredEngineers.slice(0, limit)
  }

  const pinnedEntry = filteredEngineers.find((engineer) => engineer.engineer === pinnedEngineer)
  const remainingEntries = filteredEngineers.filter((engineer) => engineer.engineer !== pinnedEngineer)

  if (!pinnedEntry) {
    return filteredEngineers.slice(0, limit)
  }

  return [pinnedEntry, ...remainingEntries.slice(0, Math.max(0, limit - 1))]
}

function renderScoreboard(scoreboard) {
  const root = document.getElementById("scoreboard-list")
  if (!root || !scoreboard?.engineers?.length) {
    return
  }

  root.innerHTML = ""

  for (const engineer of scoreboard.engineers) {
    const row = document.createElement("div")
    row.className = "scoreboard-row"
    row.dataset.engineer = engineer.engineer

    const name = document.createElement("div")
    name.className = "scoreboard-name"
    name.textContent = engineer.engineer

    const score = document.createElement("div")
    score.className = "scoreboard-score"
    score.textContent = engineer.overall_score.toFixed(3)

    row.appendChild(name)
    row.appendChild(score)
    row.addEventListener("mouseenter", (event) => {
      setHoveredEngineer(engineer.engineer)
      showTooltip(
        event,
        `<strong>${engineer.engineer}</strong><span>Overall score: ${engineer.overall_score.toFixed(3)}</span>`
      )
    })
    row.addEventListener("mousemove", moveTooltip)
    row.addEventListener("mouseleave", () => {
      setHoveredEngineer(null)
      hideTooltip()
    })
    row.addEventListener("click", () => togglePinnedEngineer(engineer.engineer))
    root.appendChild(row)
  }

  syncInteractionStyles()
}

function renderLegend(items) {
  const legend = document.getElementById("balance-legend")
  if (!legend) {
    return
  }

  legend.innerHTML = ""

  for (const item of items) {
    const wrapper = document.createElement("div")
    wrapper.className = "legend-item"

    const swatch = document.createElement("span")
    swatch.className = "legend-swatch"
    swatch.style.background = item.color

    const label = document.createElement("span")
    label.textContent = item.label

    wrapper.appendChild(swatch)
    wrapper.appendChild(label)
    legend.appendChild(wrapper)
  }
}

function renderBalanceChart(balanceView) {
  const chartRoot = document.getElementById("balance-chart")
  if (!chartRoot || !window.d3) {
    return
  }

  if (!balanceView?.engineers?.length) {
    return
  }

  const colors = {
    review_count: "#8d6c7b",
    commit_count: "#d89d61",
    pr_count: "#8cb7a0",
  }

  const chartData = buildVisibleChartData(
    balanceView.engineers,
    12,
    (engineer) => engineer.review_count + engineer.commit_count + engineer.pr_count > 0
  )

  const width = chartRoot.clientWidth || 520
  const height = 310
  const margin = { top: 8, right: 18, bottom: 90, left: 46 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom

  const svg = d3.select(chartRoot)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)

  renderLegend([
    { label: "Reviews", color: colors.review_count },
    { label: "Commits", color: colors.commit_count },
    { label: "PRs", color: colors.pr_count },
  ])

  const stack = d3
    .stack()
    .keys(["review_count", "commit_count", "pr_count"])(chartData)

  const x = d3
    .scaleBand()
    .domain(chartData.map((d) => d.engineer))
    .range([margin.left, margin.left + innerWidth])
    .padding(0.22)

  const yMax = d3.max(chartData, (d) => d.review_count + d.commit_count + d.pr_count) || 1
  const y = d3
    .scaleLinear()
    .domain([0, yMax])
    .nice()
    .range([margin.top + innerHeight, margin.top])

  const grid = svg.append("g")
  grid
    .selectAll("line")
    .data(y.ticks(4))
    .join("line")
    .attr("class", "grid-line")
    .attr("x1", margin.left)
    .attr("x2", margin.left + innerWidth)
    .attr("y1", (d) => y(d))
    .attr("y2", (d) => y(d))

  const groups = svg
    .append("g")
    .selectAll("g")
    .data(stack)
    .join("g")
    .attr("fill", (d) => colors[d.key])

  groups
    .selectAll("rect")
    .data((d) => d)
    .join("rect")
    .attr("class", "interactive-mark")
    .attr("data-engineer", (d) => d.data.engineer)
    .attr("x", (d) => x(d.data.engineer))
    .attr("y", (d) => y(d[1]))
    .attr("height", (d) => Math.max(0, y(d[0]) - y(d[1])))
    .attr("width", x.bandwidth())
    .attr("rx", 6)
    .on("mouseenter", function (event, d) {
      setHoveredEngineer(d.data.engineer)
      showTooltip(
        event,
        `<strong>${d.data.engineer}</strong>Reviews: ${d.data.review_count}<br>Commits: ${d.data.commit_count}<br>PRs: ${d.data.pr_count}<br>Balance score: ${d.data.balance_score.toFixed(3)}`
      )
    })
    .on("mousemove", moveTooltip)
    .on("mouseleave", function () {
      setHoveredEngineer(null)
      hideTooltip()
    })
    .on("click", (_, d) => togglePinnedEngineer(d.data.engineer))

  svg
    .append("g")
    .attr("transform", `translate(0, ${margin.top + innerHeight})`)
    .call(d3.axisBottom(x))
    .call((g) =>
      g
        .selectAll("text")
        .attr("transform", "rotate(-32)")
        .style("text-anchor", "end")
        .attr("dx", "-0.5em")
        .attr("dy", "0.7em")
    )

  svg
    .append("g")
    .attr("transform", `translate(${margin.left}, 0)`)
    .call(d3.axisLeft(y).ticks(4))

  svg
    .append("text")
    .attr("class", "axis-label")
    .attr("x", margin.left)
    .attr("y", height - 12)
    .text("Top engineers by current balance activity")

  syncInteractionStyles()
}

function renderThroughputChart(throughput) {
  const chartRoot = document.getElementById("throughput-chart")
  if (!chartRoot || !window.d3 || !throughput?.engineers?.length) {
    return
  }

  const chartData = buildVisibleChartData(
    throughput.engineers,
    10,
    (engineer) => engineer.opened_pr_count > 0
  )

  const width = chartRoot.clientWidth || 420
  const rowHeight = 24
  const height = Math.max(250, chartData.length * rowHeight + 70)
  const margin = { top: 14, right: 18, bottom: 32, left: 110 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom

  const svg = d3.select(chartRoot)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)

  const x = d3.scaleLinear().domain([0, 1]).range([margin.left, margin.left + innerWidth])
  const y = d3
    .scalePoint()
    .domain(chartData.map((d) => d.engineer))
    .range([margin.top, margin.top + innerHeight])
    .padding(0.8)

  svg
    .append("g")
    .selectAll("line")
    .data(chartData)
    .join("line")
    .attr("class", "dot-track")
    .attr("x1", x(0))
    .attr("x2", x(1))
    .attr("y1", (d) => y(d.engineer))
    .attr("y2", (d) => y(d.engineer))

  svg
    .append("g")
    .selectAll("circle")
    .data(chartData)
    .join("circle")
    .attr("class", "dot-mark interactive-mark")
    .attr("data-engineer", (d) => d.engineer)
    .attr("cx", (d) => x(d.throughput_ratio))
    .attr("cy", (d) => y(d.engineer))
    .attr("r", 6)
    .on("mouseenter", function (event, d) {
      setHoveredEngineer(d.engineer)
      showTooltip(
        event,
        `<strong>${d.engineer}</strong>Opened PRs: ${d.opened_pr_count}<br>Merged PRs: ${d.merged_pr_count}<br>Throughput: ${(d.throughput_ratio * 100).toFixed(1)}%`
      )
    })
    .on("mousemove", moveTooltip)
    .on("mouseleave", function () {
      setHoveredEngineer(null)
      hideTooltip()
    })
    .on("click", (_, d) => togglePinnedEngineer(d.engineer))

  svg
    .append("g")
    .attr("transform", `translate(0, ${margin.top + innerHeight})`)
    .call(d3.axisBottom(x).ticks(5).tickFormat(d3.format(".0%")))

  svg.append("g").attr("transform", `translate(${margin.left}, 0)`).call(d3.axisLeft(y))

  syncInteractionStyles()
}

function renderMergeTimeChart(mergeTime) {
  const chartRoot = document.getElementById("merge-time-chart")
  if (!chartRoot || !window.d3 || !mergeTime?.engineers?.length) {
    return
  }

  const chartData = buildVisibleChartData(mergeTime.engineers, 8)
  const width = chartRoot.clientWidth || 420
  const rowHeight = 24
  const height = Math.max(180, chartData.length * rowHeight + 60)
  const margin = { top: 10, right: 18, bottom: 30, left: 120 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom

  const svg = d3.select(chartRoot)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)

  const xMax = d3.max(chartData, (d) => d.average_open_to_merge_days) || 1
  const x = d3.scaleLinear().domain([0, xMax]).nice().range([margin.left, margin.left + innerWidth])
  const y = d3
    .scaleBand()
    .domain(chartData.map((d) => d.engineer))
    .range([margin.top, margin.top + innerHeight])
    .padding(0.25)

  svg
    .append("g")
    .selectAll("rect")
    .data(chartData)
    .join("rect")
    .attr("class", "bar-mark interactive-mark")
    .attr("data-engineer", (d) => d.engineer)
    .attr("x", margin.left)
    .attr("y", (d) => y(d.engineer))
    .attr("width", (d) => x(d.average_open_to_merge_days) - margin.left)
    .attr("height", y.bandwidth())
    .attr("rx", 6)
    .on("mouseenter", function (event, d) {
      setHoveredEngineer(d.engineer)
      showTooltip(
        event,
        `<strong>${d.engineer}</strong>Average: ${d.average_open_to_merge_days} days<br>Median: ${d.median_open_to_merge_days} days<br>Merged PRs: ${d.merged_pr_count}`
      )
    })
    .on("mousemove", moveTooltip)
    .on("mouseleave", function () {
      setHoveredEngineer(null)
      hideTooltip()
    })
    .on("click", (_, d) => togglePinnedEngineer(d.engineer))

  svg
    .append("g")
    .attr("transform", `translate(0, ${margin.top + innerHeight})`)
    .call(d3.axisBottom(x).ticks(4).tickFormat((d) => `${d}d`))

  svg.append("g").attr("transform", `translate(${margin.left}, 0)`).call(d3.axisLeft(y))

  syncInteractionStyles()
}

function renderChangeSurfaceChart(changeSurface) {
  const chartRoot = document.getElementById("change-surface-chart")
  if (!chartRoot || !window.d3 || !changeSurface?.engineers?.length) {
    return
  }

  const chartData = buildVisibleChartData(changeSurface.engineers, 8)
  const width = chartRoot.clientWidth || 420
  const rowHeight = 24
  const height = Math.max(180, chartData.length * rowHeight + 60)
  const margin = { top: 10, right: 18, bottom: 30, left: 130 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom

  const svg = d3.select(chartRoot)
  svg.selectAll("*").remove()
  svg.attr("viewBox", `0 0 ${width} ${height}`)

  const xMax = d3.max(chartData, (d) => d.change_surface_score) || 1
  const x = d3.scaleLinear().domain([0, xMax]).nice().range([margin.left, margin.left + innerWidth])
  const y = d3
    .scaleBand()
    .domain(chartData.map((d) => d.engineer))
    .range([margin.top, margin.top + innerHeight])
    .padding(0.25)

  svg
    .append("g")
    .selectAll("rect")
    .data(chartData)
    .join("rect")
    .attr("class", "bar-mark-alt interactive-mark")
    .attr("data-engineer", (d) => d.engineer)
    .attr("x", margin.left)
    .attr("y", (d) => y(d.engineer))
    .attr("width", (d) => x(d.change_surface_score) - margin.left)
    .attr("height", y.bandwidth())
    .attr("rx", 6)
    .on("mouseenter", function (event, d) {
      setHoveredEngineer(d.engineer)
      showTooltip(
        event,
        `<strong>${d.engineer}</strong>Lines changed: ${d.lines_changed}<br>Files changed: ${d.files_changed}<br>Impact score: ${d.change_surface_score}`
      )
    })
    .on("mousemove", moveTooltip)
    .on("mouseleave", function () {
      setHoveredEngineer(null)
      hideTooltip()
    })
    .on("click", (_, d) => togglePinnedEngineer(d.engineer))

  svg
    .append("g")
    .attr("transform", `translate(0, ${margin.top + innerHeight})`)
    .call(d3.axisBottom(x).ticks(4))

  svg.append("g").attr("transform", `translate(${margin.left}, 0)`).call(d3.axisLeft(y))

  syncInteractionStyles()
}

async function hydrateDashboard() {
  try {
    const [gitHistory, openPrs, balanceView, throughput, mergeTime, changeSurface, scoreboard] = await Promise.all([
      loadJson("analysis/results/git-history-summary.json"),
      loadJson("analysis/results/open-pr-summary.json"),
      loadJson("analysis/results/balance-view.json"),
      loadJson("analysis/results/throughput.json"),
      loadJson("analysis/results/merge-time.json"),
      loadJson("analysis/results/change-surface.json"),
      loadJson("analysis/results/scoreboard.json"),
    ])
    dashboardCache.scoreboard = scoreboard
    dashboardCache.balanceView = balanceView
    dashboardCache.throughput = throughput
    dashboardCache.mergeTime = mergeTime
    dashboardCache.changeSurface = changeSurface

    renderScoreboard(scoreboard)
    renderBalanceChart(balanceView)
    renderThroughputChart(throughput)
    renderMergeTimeChart(mergeTime)
    renderChangeSurfaceChart(changeSurface)
  } catch (error) {
    const scoreboard = document.getElementById("scoreboard-list")
    if (scoreboard) {
      scoreboard.innerHTML = "<div class='scoreboard-row'><div class='scoreboard-name'>Run dashboard data build</div><div class='scoreboard-score'>—</div></div>"
    }
  }
}

hydrateDashboard()
