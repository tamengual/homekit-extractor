#import <Foundation/Foundation.h>

/// Safely reads KVC values from objects without crashing on undefined keys.
/// NSObject.value(forKey:) raises NSUndefinedKeyException for unknown keys,
/// which crashes in Swift. This ObjC helper catches the exception.
@interface SafeKVCReader : NSObject
+ (nullable id)valueForKey:(NSString *)key onObject:(NSObject *)object;
@end

@implementation SafeKVCReader

+ (nullable id)valueForKey:(NSString *)key onObject:(NSObject *)object {
    @try {
        return [object valueForKey:key];
    }
    @catch (NSException *exception) {
        return nil;
    }
}

@end
