import SwiftUI

struct ContentView: View {
    @StateObject private var exporter = HomeKitExporter()

    var body: some View {
        VStack(spacing: 20) {
            Text("HomeKit Dumper")
                .font(.largeTitle)
                .fontWeight(.bold)
                .padding(.top, 40)

            Text("Export all HomeKit automations, scenes, accessories, and rooms to JSON")
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            Button(action: {
                exporter.startExport()
            }) {
                Text("Export HomeKit Data")
                    .font(.title2)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(exporter.isExporting ? Color.gray : Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(12)
            }
            .disabled(exporter.isExporting)
            .padding(.horizontal, 40)

            if exporter.isExporting {
                ProgressView("Working...")
                    .padding()
            }

            Text(exporter.statusMessage)
                .font(.callout)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            if let filePath = exporter.outputFilePath {
                VStack(spacing: 4) {
                    Text("File saved to:")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(filePath)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundColor(.blue)
                        .textSelection(.enabled)
                }
                .padding()
                .background(Color(.systemGray6))
                .cornerRadius(8)
                .padding(.horizontal)
            }

            Spacer()
        }
        .padding()
    }
}
