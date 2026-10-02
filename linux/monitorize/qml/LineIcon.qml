import QtQuick

Canvas {
    id: icon
    property string symbol: "display"
    property color tint: "#a6caff"
    implicitWidth: 24
    implicitHeight: 24
    onSymbolChanged: requestPaint()
    onTintChanged: requestPaint()
    onPaint: {
        let c = getContext("2d")
        c.reset()
        c.scale(width / 24, height / 24)
        c.strokeStyle = tint
        c.lineWidth = 1.7
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(x, y, a, b) { c.moveTo(x, y); c.lineTo(a, b) }
        c.beginPath()
        if (symbol === "display" || symbol === "session") {
            c.rect(3, 4, 18, 13)
            line(12, 17, 12, 21); line(8, 21, 16, 21)
            if (symbol === "session") {
                c.moveTo(10, 8); c.lineTo(15, 11); c.lineTo(10, 14); c.closePath()
            }
        } else if (symbol === "settings") {
            c.arc(12, 12, 6, 0, Math.PI * 2)
            c.moveTo(14, 12); c.arc(12, 12, 2, 0, Math.PI * 2)
            for (let i = 0; i < 8; i++) {
                let a = i * Math.PI / 4
                line(12 + 6 * Math.cos(a), 12 + 6 * Math.sin(a),
                     12 + 9 * Math.cos(a), 12 + 9 * Math.sin(a))
            }
        } else if (symbol === "extras") {
            // Sliders represent optional controls without implying gamepad support.
            line(5, 3, 5, 21); line(12, 3, 12, 21); line(19, 3, 19, 21)
            c.stroke(); c.beginPath()
            c.fillStyle = "#172638"
            c.rect(2, 7, 6, 4); c.rect(9, 14, 6, 4); c.rect(16, 6, 6, 4)
            c.fill()
        } else if (symbol === "streaming") {
            c.arc(12, 9, 2, 0, Math.PI * 2)
            line(12, 11, 12, 21)
            c.moveTo(7, 4); c.bezierCurveTo(3, 7, 3, 12, 7, 15)
            c.moveTo(17, 4); c.bezierCurveTo(21, 7, 21, 12, 17, 15)
        } else if (symbol === "plus") {
            line(12, 4, 12, 20); line(4, 12, 20, 12)
        } else if (symbol === "trash") {
            line(4, 7, 20, 7)
            line(9, 7, 9, 4); line(9, 4, 15, 4); line(15, 4, 15, 7)
            line(6, 9, 7, 20); line(7, 20, 17, 20); line(17, 20, 18, 9)
            line(10, 11, 10, 17); line(14, 11, 14, 17)
        } else if (symbol === "bookmark") {
            c.moveTo(6, 3); c.lineTo(6, 21); c.lineTo(12, 17)
            c.lineTo(18, 21); c.lineTo(18, 3); c.closePath()
        } else if (symbol === "link") {
            c.moveTo(10, 8); line(12, 6, 14, 4)
            c.bezierCurveTo(18, 1, 23, 6, 20, 10)
            line(20, 10, 16, 14)
            c.moveTo(14, 16); line(12, 18, 10, 20)
            c.bezierCurveTo(6, 23, 1, 18, 4, 14)
            line(4, 14, 8, 10); line(8, 10, 16, 10)
        } else if (symbol === "play") {
            c.moveTo(7, 4); c.lineTo(7, 20); c.lineTo(20, 12); c.closePath()
            c.fillStyle = tint; c.fill()
        } else if (symbol === "stop") {
            c.rect(5, 5, 14, 14)
            c.fillStyle = tint; c.fill()
        } else {
            c.rect(5, 3, 14, 18)
            line(8, 8, 16, 8); line(8, 12, 16, 12); line(8, 16, 13, 16)
        }
        c.stroke()
    }
}
