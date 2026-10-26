/// HID wheel signs shared by native and portable controls: positive advances content.
public enum FeedDirection {
    public static func wheelDelta(items: Int, amount: Int) -> Int? {
        guard (items == 1 || items == -1), (1...1200).contains(amount) else { return nil }
        return items * amount
    }
}
